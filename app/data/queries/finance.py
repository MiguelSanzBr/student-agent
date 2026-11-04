"""Consultas financeiras (sob RLS; a matrícula chega por enrollment_key, nunca pelo pagador)."""

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.rules.finance import Invoice, LateCharge, Payment


@dataclass(frozen=True)
class LedgerInvoice(Invoice):
    competence: str


_STATUS_PT = {
    "PAID": "paga", "PARTIAL": "parcial", "RENEGOTIATED": "renegociada",
    "IN_COLLECTION": "Em cobrança", "CANCELLED": "cancelada",
}  # fmt: skip


def status_label(status: str, due: date, today: date) -> str:
    """A data de referência manda: aberta com vencimento passado é 'vencida'."""
    if status in ("OPEN", "OVERDUE"):
        return "vencida" if due < today else "aberta"
    return _STATUS_PT.get(status, status.lower())


async def fetch_ledger(
    conn: AsyncConnection[Any], enrollment_key: UUID
) -> tuple[list[LedgerInvoice], list[Payment], list[LateCharge]]:
    cur = await conn.execute(
        "SELECT invoice_id, net_cents, status, due_date, competence FROM canon.invoice"
        " WHERE enrollment_key = %s ORDER BY due_date DESC, invoice_id",
        (enrollment_key,),
    )
    invoices = [LedgerInvoice(r[0], r[1], r[2], r[3], r[4]) for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT invoice_id, principal_cents, fine_cents, interest_cents, status"
        " FROM canon.payment WHERE enrollment_key = %s",
        (enrollment_key,),
    )
    payments = [Payment(r[0], r[1] or 0, r[2] or 0, r[3] or 0, r[4]) for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT invoice_id, coalesce(fine_cents,0) + coalesce(interest_cents,0),"
        " coalesce(waived,false)"
        " FROM canon.late_charge WHERE enrollment_key = %s",
        (enrollment_key,),
    )
    charges = [LateCharge(r[0], r[1], r[2]) for r in await cur.fetchall()]
    return invoices, payments, charges


async def fetch_agreements(
    conn: AsyncConnection[Any], enrollment_key: UUID
) -> list[tuple[Any, ...]]:
    cur = await conn.execute(
        "SELECT a.agreement_id, a.status, a.installments, a.original_debt_cents, a.waived_cents,"
        " a.agreed_amount_cents, a.created_at::date,"
        " (SELECT count(*) FROM canon.agreement_invoice ai WHERE ai.agreement_id = a.agreement_id"
        "   AND ai.enrollment_key = a.enrollment_key)"
        " FROM canon.agreement a WHERE a.enrollment_key = %s ORDER BY a.created_at DESC",
        (enrollment_key,),
    )
    return list(await cur.fetchall())


async def fetch_discounts(
    conn: AsyncConnection[Any], enrollment_key: UUID
) -> list[tuple[Any, ...]]:
    cur = await conn.execute(
        "SELECT discount_id, discount_type, rate, valid_from, valid_to FROM canon.discount"
        " WHERE enrollment_key = %s ORDER BY valid_from DESC NULLS LAST, discount_id",
        (enrollment_key,),
    )
    return list(await cur.fetchall())


async def fetch_coverage_start(conn: AsyncConnection[Any], domain: str) -> date | None:
    cur = await conn.execute(
        "SELECT min(starts_on) FROM canon.coverage WHERE domain = %s", (domain,)
    )
    row = await cur.fetchone()
    return row[0] if row else None
