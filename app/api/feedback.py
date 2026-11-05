"""POST /feedback: 👍/👎; o 👎 guarda por 7 dias só as saídas tipadas do turno (nunca texto)."""

import contextlib
import json
import re
from datetime import timedelta
from functools import cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import get_person_cookie
from app.api.protocols import same_origin
from app.data.db import with_scope
from app.infra.stores.turn_outputs import TurnOutputCache
from app.obs.annotations import annotate
from app.obs.metrics import METRICS
from core.clock import wall_now

router = APIRouter()
RETENTION = timedelta(days=7)
_SERVICE = re.compile(r"[A-Z]{3}-\d{3}")
_OMITTED = "[omitido]"


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    useful: bool  # sem texto livre: comentário seria dado pessoal e injeção para quem analisa


@cache
def _props() -> dict[str, dict[str, Any]]:
    from app.tools.registry import build_tools

    return {t.spec.name: dict(t.spec.parameters["properties"]) for t in build_tools()}


def _enumerated(tool: str, key: str, value: str) -> bool:
    """Só vale o que o servidor consegue enumerar (enum/padrão do schema ou código de serviço)."""
    prop = _props().get(tool, {}).get(key, {})
    if "enum" in prop:
        return value in prop["enum"]
    if "pattern" in prop:
        return re.fullmatch(prop["pattern"], value) is not None
    return key == "service_code" and _SERVICE.fullmatch(value) is not None


ALLOWED_KEYS = ("status", "guard", "answer_kind", "domain", "tools_class", "model",
                "tool_calls", "records")  # fmt: skip
_ATOMIC = re.compile(r"[\d.,/:$%R -]{1,32}|[A-Za-z0-9_-]{1,32}")  # número, data, valor ou rótulo


def typed_only(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist: sem a resposta, sem argumento livre e sem campo de registro em texto livre."""
    out = {k: payload[k] for k in ALLOWED_KEYS if k in payload}
    out["tool_calls"] = [
        {"name": c["name"], "returned_records": c.get("returned_records"),
         "args": {k: v if _enumerated(c["name"], k, str(v)) else _OMITTED
                  for k, v in c.get("args", {}).items()}}
        for c in payload.get("tool_calls", [])
    ]  # fmt: skip
    out["records"] = [
        {"kind": r["kind"], "id": r["id"],
         "fields": {k: v if _ATOMIC.fullmatch(str(v)) else _OMITTED
                    for k, v in r.get("fields", {}).items()}}
        for r in payload.get("records", [])
    ]  # fmt: skip
    return out


@router.post("/feedback", dependencies=[Depends(same_origin)])
async def feedback(
    body: FeedbackRequest, request: Request, pk: Annotated[UUID, Depends(get_person_cookie)]
) -> JSONResponse:
    st = request.app.state
    # o cache é por aluno: só o dono do turno avalia (👍 ou 👎); trace alheio não grava nem anota
    payload = await TurnOutputCache(st.redis).get(pk, body.trace_id)
    if payload is None:
        return JSONResponse({"mensagem": "Turno não encontrado."}, status_code=404)
    now = wall_now()
    async with with_scope(st.pool, pk, ()) as conn:
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO agent_rw.feedback (trace_id, person_key, useful)"
                    " VALUES (%s, %s, %s)",
                    (body.trace_id, pk, body.useful),
                )
        except errors.UniqueViolation:  # um voto por turno: o repetido não grava nem anota
            return JSONResponse({"status": "ok"})
        if not body.useful:
            # agent_app não tem SELECT: ON CONFLICT não serve; o 👎 repetido vira UniqueViolation
            with contextlib.suppress(errors.UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO agent_rw.turn_output (trace_id, person_key, created_at,"
                        " expires_at, payload) VALUES (%s, %s, %s, %s, %s::jsonb)",
                        (body.trace_id, pk, now, now + RETENTION,
                         json.dumps(typed_only(dict(payload)))),
                    )  # fmt: skip
    METRICS["agent_feedback_total"].labels(useful=str(body.useful).lower()).inc()  # type: ignore[union-attr]
    annotate(body.trace_id, "feedback", "up" if body.useful else "down",
             1.0 if body.useful else 0.0, None)  # fmt: skip
    return JSONResponse({"status": "ok"})
