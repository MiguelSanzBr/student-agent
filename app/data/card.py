"""Cartão do aluno (spec §6.2): entregue como functionResponse sintético; sem o nome do aluno."""

from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.tools import academic, finance, lms, protocols
from app.tools.base import DraftCard, ToolContext, default_ontology
from core.format import fmt_date
from core.scope import StudentScope

if TYPE_CHECKING:
    from psycopg import AsyncConnection

    from app.config import AppSettings

# Provisório: o spike do Gemini não rodou (sem chave). 3,5 é conservador para PT-BR.
CHARS_PER_TOKEN = 3.5
_CLOSED = frozenset({"resolvido", "encerrado", "cancelado"})
_PAYER_ORDER = ("responsavel", "empresa", "aluno")


@dataclass(frozen=True)
class StudentCard:
    text: str
    facts: Mapping[str, str]  # chaves "<índice>.<campo>" e "geral.<campo>"


def _d(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:  # "não informado" (protocolo sem prazo)
        return None


async def _payer(conn: "AsyncConnection[Any]", ek: Any) -> str | None:
    cur = await conn.execute(
        "SELECT DISTINCT payer_type FROM canon.contract WHERE enrollment_key = %s", (ek,)
    )
    got = {r[0] for r in await cur.fetchall()}
    kind = next((k for k in _PAYER_ORDER if k in got), None)
    return default_ontology().label_pt("payer_type", kind).casefold() if kind else None


async def build_student_card(
    conn: "AsyncConnection[Any]",
    scope: StudentScope,
    settings: "AppSettings",
    *,
    today: date,
    pending: DraftCard | None = None,
) -> StudentCard:
    @asynccontextmanager
    async def db() -> Any:
        yield conn

    ctx = ToolContext(
        scope=scope, settings=settings, channel="web", conversation_id="", tainted=False,
        round=1, offer_other_enrollment=False, today=today, db=db,
        drafts=None,  # type: ignore[arg-type]  # o cartão só lê
    )  # fmt: skip
    onto = default_ontology()
    facts = {"geral.data_referencia": fmt_date(today)}
    lines = [f"Data de referência: {facts['geral.data_referencia']}"]
    for e in scope.enrollments:
        i = e.index
        lines.append(f"{i}. {e.label} ({onto.label_pt('enrollment_status', e.status).lower()})")
        fin = (await finance.get_financial_summary(ctx, {"enrollment": i})).data
        for k in ("total_para_quitar", "proximo_vencimento", "valor_proximo_vencimento",
                  "faturas_vencidas"):  # fmt: skip
            facts[f"{i}.{k}"] = str(fin[k])
        parts = [f"total para quitar {fin['total_para_quitar']}",
                 f"vencidas {fin['faturas_vencidas']}"]  # fmt: skip
        if fin["proximo_vencimento"] != "nenhum":
            parts.append(f"próximo vencimento {fin['valor_proximo_vencimento']} em "
                         f"{fin['proximo_vencimento']}")  # fmt: skip
        if payer := await _payer(conn, e.enrollment_key):
            facts[f"{i}.pagador"] = payer
            parts.append(f"pagador {payer}")
        lines.append("   " + "; ".join(parts))
        if e.status == "GRADUATED":
            prof = (await academic.get_academic_profile(ctx, {"enrollment": i})).data
            for doc in prof["documentos"]:
                if doc["tipo"] == onto.label_pt("document_type", "Diploma"):
                    facts[f"{i}.diploma_emitido_em"] = doc["emitido_em"]
                    lines.append(f"   Diploma emitido em {doc['emitido_em']}")
                    break
        act = (await lms.get_lms_activity(ctx, {"enrollment": i})).data
        if "simulados_abertos" in act:
            facts[f"{i}.simulados_abertos"] = str(act["simulados_abertos"])
            parts = [f"simulados abertos {act['simulados_abertos']}"]
            if nxt := act["proxima_avaliacao"]:
                facts[f"{i}.proxima_avaliacao"] = nxt["data"]
                parts.insert(
                    0, f"próxima avaliação {nxt['data']} ({nxt['tipo']}, fonte: {nxt['fonte']})"
                )
            lines.append("   " + "; ".join(parts))
    prots = (await protocols._list(ctx, {})).data.get("protocolos", [])
    open_ = [p for p in prots if p["status"].casefold() not in _CLOSED]
    facts["geral.protocolos_abertos"] = str(len(open_))
    line = f"Protocolos abertos: {len(open_)}"
    if prazos := [d for p in open_ if (d := _d(p["prazo"]))]:
        facts["geral.proximo_prazo"] = fmt_date(min(prazos))
        line += f"; próximo prazo {facts['geral.proximo_prazo']}"
    lines.append(line)
    if pending is not None:
        lines.append(f"Pedido de {pending.service_name_pt} aguardando Confirmar")
    return StudentCard("\n".join(lines).replace("*", "").replace("#", ""), facts)
