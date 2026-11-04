"""POST /protocols/confirm: o clique humano abre o pedido do rascunho montado pelo servidor."""

import logging
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agent.llm import Message
from app.agent.messages import CONFIRMED_TEMPLATE
from app.api import chat
from app.api.auth import get_person_cookie
from app.data.db import with_scope
from app.data.queries import protocols as q
from app.infra.stores.drafts import DraftStore
from app.infra.stores.history import HistoryStore
from app.obs.metrics import METRICS
from app.rules.eligibility import Eligible
from app.rules.protocols import sla_due
from app.tools.base import ToolError, default_ontology, resolve_enrollment
from app.tools.protocols import _BY_SUBJECT, check_eligibility
from core.clock import reference_today
from core.format import fmt_date

router = APIRouter()
log = logging.getLogger(__name__)
NO_LOCK = frozenset({"GEN-090", "OUV-099"})  # transbordo: pode repetir
# A chave do lock é calculada no SQL (estável entre processos); nunca com o hash do Python.
LOCK_KEY_SQL = (
    "SELECT hashtextextended(%(person_key)s::text || ':' || %(service)s || ':'"
    " || coalesce(%(subject)s, ''), 0)"
)
LOCK_SQL = LOCK_KEY_SQL.replace("SELECT ", "SELECT pg_advisory_xact_lock(", 1) + ")"
INSERT_SQL = (
    "INSERT INTO agent_rw.protocol_request (id, person_key, enrollment_index, service_code,"
    " subject_code, description, draft_id, opened_on, prazo)"
    " VALUES ('SA-' || %(year)s::text || '-' ||"
    " lpad(nextval('agent_rw.protocol_request_seq')::text, 6, '0'),"
    " %(person_key)s, %(index)s, %(service)s, %(subject)s, %(description)s,"
    " %(draft_id)s, %(opened)s, %(prazo)s) ON CONFLICT (draft_id) DO NOTHING RETURNING id"
)


async def same_origin(request: Request) -> None:
    """CSRF: só POST JSON do próprio site (o cookie é SameSite=Strict; isto é a 2ª camada)."""
    origin = request.headers.get("origin")
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if (
        not origin
        or urlsplit(origin).netloc != request.headers.get("host")
        or (ctype != "application/json")
    ):
        raise HTTPException(status_code=403, detail="origem não permitida")


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


@router.post("/protocols/confirm", dependencies=[Depends(same_origin)])
async def confirm(
    body: ConfirmRequest, request: Request, pk: Annotated[UUID, Depends(get_person_cookie)]
) -> dict[str, str]:
    st = request.app.state
    card = await DraftStore(st.redis, st.settings.limits).take(pk, body.draft_id)
    if card is None:  # vencido, já usado ou de outro aluno (o sub entra na chave): indistinguível
        raise _conflict("Esse rascunho venceu ou já foi usado. Peça de novo no chat.")
    history = HistoryStore(st.redis, st.settings.limits)
    if (await history.load(pk, "web", card.conversation_id)).tainted:
        raise _conflict("Não posso abrir pedidos nesta conversa. Comece uma nova.")
    code, subject = card.key.service_code, card.key.subject_code
    svc = default_ontology().service(code)
    if svc.mode != "agent":
        raise _conflict("Este serviço não é aberto pelo assistente.")
    scope = await chat._load_scope(request, pk)
    try:
        enr = resolve_enrollment(scope, card.enrollment_index, None)
    except ToolError:
        raise _conflict("Matrícula não encontrada.") from None
    today = reference_today(st.settings)
    prazo = sla_due(today, svc.sla_hours)
    description = (body.description if body.description is not None else card.description).strip()
    async with with_scope(st.pool, pk, scope.brand_families) as conn:
        if code not in NO_LOCK:  # serializa confirmações do mesmo aluno, serviço e disciplina
            await conn.execute(
                LOCK_SQL, {"person_key": str(pk), "service": code, "subject": subject}
            )
        rows = await q.class_results(conn, enr.enrollment_key) if code in _BY_SUBJECT else []
        term = await q.current_term(conn)
        verdict = await check_eligibility(conn, code, svc, enr, subject, today, rows, term)
        if not isinstance(verdict, Eligible):  # já com o lock: enxerga o pedido recém-commitado
            raise _conflict(verdict.motivo_pt)
        row = await (
            await conn.execute(
                INSERT_SQL,
                {
                    "year": today.year,
                    "person_key": pk,
                    "index": enr.index,
                    "service": code,
                    "subject": subject,
                    "description": description[:500],
                    "draft_id": card.draft_id,
                    "opened": today,
                    "prazo": prazo,
                },
            )  # fmt: skip
        ).fetchone()
        if row is None:  # mesmo draft_id já gravado: devolve o pedido existente
            cur = await conn.execute(
                "SELECT id FROM agent_rw.protocol_request WHERE draft_id = %s", (card.draft_id,)
            )
            row = await cur.fetchone()
    sa, due = str(row[0]), fmt_date(prazo)  # type: ignore[index]
    note = Message("system_note", f"Solicitação {sa} aberta em {fmt_date(today)}")
    await history.append(pk, "web", card.conversation_id, (note,), draft_only=False)
    METRICS["agent_draft_total"].labels(outcome="confirmado").inc()  # type: ignore[union-attr]
    return {"sa": sa, "prazo": due, "mensagem": CONFIRMED_TEMPLATE.format(sa=sa, prazo=due)}


@router.get("/protocols")
async def protocols_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
