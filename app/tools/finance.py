import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.data.queries import finance as q
from app.rules.coverage import coverage_note
from app.rules.finance import financial_position, invoice_balance, invoice_to_settle
from app.tools.base import (
    ENROLLMENT,
    EntityRecord,
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    resolve_enrollment,
    schema,
    validate_args,
)
from core.format import fmt_brl, fmt_date, fmt_pct

CAP = 20
EMPTY = "Nenhum registro encontrado."
_DISCOUNT_PT = {
    "CORPORATE": "corporativo",
    "MERIT": "mérito",
    "SIBLING": "irmão",
    "PROUNI_100": "ProUni 100%",
}
SUMMARY = schema({"enrollment": ENROLLMENT})
INVOICES = schema(
    {
        "enrollment": ENROLLMENT,
        "status": {"type": "string", "enum": ["aberta", "vencida", "paga", "parcial"]},
        "period": {"type": "string", "description": "Competência AAAA-MM"},
    }
)
AGREEMENTS = schema({"enrollment": ENROLLMENT})
SUMMARY_SPEC = ToolSpec(
    "get_financial_summary",
    "Resumo financeiro da matrícula: total para quitar, vencidos e próximo vencimento.",
    SUMMARY,
)
INVOICES_SPEC = ToolSpec(
    "list_invoices",
    "Lista faturas da matrícula, com vencimento, saldo e valor para quitar.",
    INVOICES,
)
AGREEMENTS_SPEC = ToolSpec(
    "list_agreements", "Lista acordos de renegociação da matrícula.", AGREEMENTS
)
_AGREEMENT_PT = {"ACTIVE": "ativo", "BROKEN": "descumprido", "SETTLED": "quitado"}


async def _summary(
    ctx: ToolContext, idx: str
) -> tuple[dict[str, Any], EntityRecord, list[EntityRecord], int]:
    e = resolve_enrollment(ctx.scope, idx, None)
    async with ctx.db() as conn:
        inv, pay, lc = await q.fetch_ledger(conn, e.enrollment_key)
        disc = await q.fetch_discounts(conn, e.enrollment_key)
    p = financial_position(inv, pay, lc, ctx.today)
    d: dict[str, Any] = {
        "total_para_quitar": fmt_brl(p.total_due),
        "principal_vencido": fmt_brl(p.overdue_principal),
        "encargos": fmt_brl(p.open_charges),
        "faturas_vencidas": p.overdue_count,
        "proximo_vencimento": fmt_date(p.next_due_date) if p.next_due_date else "nenhum",
        "valor_proximo_vencimento": fmt_brl(p.next_due_amount),
        "dias_para_proximo_vencimento": p.days_to_next_due,
    }
    rec = EntityRecord(
        "financeiro",
        e.index,
        (f"matrícula {e.index}",),
        {k: str(v) for k, v in d.items() if k != "dias_para_proximo_vencimento"},
    )
    descontos, drecs = [], []
    for did, typ, rate, vfrom, vto in disc:
        item = {
            "tipo": _DISCOUNT_PT.get(typ, typ.lower()),
            "percentual": fmt_pct(Decimal(rate) * 100),
            "vigente": "sim"
            if (vfrom is None or vfrom <= ctx.today) and (vto is None or vto >= ctx.today)
            else "não",
        }
        descontos.append(item)
        drecs.append(EntityRecord("desconto", f"{e.index}-{did}", (item["tipo"],), item))
    if descontos:
        d["descontos"] = descontos
    return d, rec, drecs, p.total_due


async def get_financial_summary(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    a = validate_args(SUMMARY_SPEC, args)
    idxs = [a["enrollment"]] if "enrollment" in a else [e.index for e in ctx.scope.enrollments]
    if len(idxs) == 1:
        d, rec, drecs, _ = await _summary(ctx, idxs[0])
        return ToolOutput(d, (rec, *drecs))
    rows, recs, total = [], [], 0
    for i in idxs:
        d, rec, drecs, due = await _summary(ctx, i)
        rows.append({"matrícula": i, **d})
        recs += [rec, *drecs]
        total += due
    return ToolOutput({"matriculas": rows, "total_geral": fmt_brl(total)}, tuple(recs))


async def list_invoices(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    a = validate_args(INVOICES_SPEC, args)
    period = a.get("period")
    if period is not None and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period):
        raise ToolError("ARGUMENTO_INVALIDO")
    e = resolve_enrollment(ctx.scope, a.get("enrollment"), None)
    async with ctx.db() as conn:
        inv, pay, lc = await q.fetch_ledger(conn, e.enrollment_key)
        since = await q.fetch_coverage_start(conn, "financeiro") if period else None
    rows = [(i, q.status_label(i.status, i.due, ctx.today)) for i in inv]
    rows = [
        (i, s)
        for i, s in rows
        if (period is None or i.competence == period) and (not a.get("status") or s == a["status"])
    ]
    items, recs = [], []
    for i, s in rows[:CAP]:
        it = {
            "fatura": i.id,
            "competência": i.competence,
            "status": s,
            "vencimento": fmt_date(i.due),
            "valor": fmt_brl(i.net),
            "saldo": fmt_brl(invoice_balance(i, pay)),
            "para_quitar": fmt_brl(invoice_to_settle(i, pay, lc)),
        }
        items.append(it)
        recs.append(
            EntityRecord(
                "fatura",
                i.id,
                (i.id, i.competence),
                {k: it[k] for k in ("status", "vencimento", "saldo", "para_quitar")},
            )
        )
    data: dict[str, Any] = {"faturas": items, "total": len(rows)}
    if not rows:
        data["mensagem"] = EMPTY
    if period and since and period < since.strftime("%Y-%m"):
        data["nota"] = coverage_note("financeiro", since)
    return ToolOutput(data, tuple(recs))


async def list_agreements(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    a = validate_args(AGREEMENTS_SPEC, args)
    e = resolve_enrollment(ctx.scope, a.get("enrollment"), None)
    async with ctx.db() as conn:
        rows = await q.fetch_agreements(conn, e.enrollment_key)
    items, recs = [], []
    for aid, status, n, orig, waived, agreed, created, n_inv in rows[:CAP]:
        st = _AGREEMENT_PT.get(status, status.lower())
        it = {
            "acordo": aid,
            "status": st,
            "parcelas": int(n),
            "divida_original": fmt_brl(orig),
            "abatimento": fmt_brl(waived),
            "valor_acordado": fmt_brl(agreed),
            "criado_em": fmt_date(created),
            "faturas_incluidas": n_inv,
        }
        items.append(it)
        recs.append(
            EntityRecord("acordo", aid, (aid,), {k: str(v) for k, v in it.items() if k != "acordo"})
        )
    data: dict[str, Any] = {"acordos": items, "total": len(rows)}
    if not rows:
        data["mensagem"] = EMPTY
    return ToolOutput(data, tuple(recs))


TOOLS: tuple[Tool, ...] = (
    Tool(SUMMARY_SPEC, get_financial_summary, "financeiro"),
    Tool(INVOICES_SPEC, list_invoices, "financeiro"),
    Tool(AGREEMENTS_SPEC, list_agreements, "financeiro"),
)
