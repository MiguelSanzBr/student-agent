"""Regras financeiras puras (spec §5.5). Valores em centavos."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

COUNTED = frozenset(
    {"OPEN", "OVERDUE", "PARTIAL"}
)  # RENEGOTIATED sai: as parcelas do acordo a substituem


@dataclass(frozen=True)
class Invoice:
    id: str
    net: int
    status: str
    due: date


@dataclass(frozen=True)
class Payment:
    invoice_id: str
    principal: int
    multa: int
    juros: int
    status: str  # CONFIRMED | REVERSED | ...


@dataclass(frozen=True)
class LateCharge:
    invoice_id: str
    amount: int
    waived: bool = False


@dataclass(frozen=True)
class Position:
    overdue_principal: int
    open_charges: int
    total_due: int
    upcoming: int
    next_due_date: date | None
    next_due_amount: int
    days_to_next_due: int | None
    overdue_count: int


def _confirmed(invoice: Invoice, payments: Iterable[Payment]) -> list[Payment]:
    return [p for p in payments if p.invoice_id == invoice.id and p.status == "CONFIRMED"]


def invoice_balance(invoice: Invoice, payments: Iterable[Payment]) -> int:
    if invoice.status not in COUNTED:
        return 0
    return max(0, invoice.net - sum(p.principal for p in _confirmed(invoice, payments)))


def open_charges(
    invoice: Invoice, payments: Iterable[Payment], late_charges: Iterable[LateCharge]
) -> int:
    if invoice.status not in COUNTED:
        return 0
    due = sum(c.amount for c in late_charges if c.invoice_id == invoice.id and not c.waived)
    paid = sum(p.multa + p.juros for p in _confirmed(invoice, payments))
    return max(0, due - paid)


def invoice_to_settle(
    invoice: Invoice, payments: Iterable[Payment], late_charges: Iterable[LateCharge]
) -> int:
    payments = list(payments)
    return invoice_balance(invoice, payments) + open_charges(invoice, payments, late_charges)


def financial_position(
    invoices: Iterable[Invoice],
    payments: Iterable[Payment],
    late_charges: Iterable[LateCharge],
    today: date,
) -> Position:
    payments, late_charges = list(payments), list(late_charges)
    counted = [i for i in invoices if i.status in COUNTED]  # encargos em aberto contam sem saldo
    overdue = [
        i for i in counted if i.due < today
    ]  # a data de referência manda, não o status gravado
    upcoming = sorted((i for i in counted if i.due >= today), key=lambda i: i.due)
    principal = sum(invoice_balance(i, payments) for i in overdue)
    charges = sum(open_charges(i, payments, late_charges) for i in overdue)
    nxt = upcoming[0] if upcoming else None
    return Position(
        overdue_principal=principal,
        open_charges=charges,
        total_due=principal + charges,
        upcoming=sum(invoice_balance(i, payments) for i in upcoming),
        next_due_date=nxt.due if nxt else None,
        next_due_amount=invoice_balance(nxt, payments) if nxt else 0,
        days_to_next_due=(nxt.due - today).days if nxt else None,
        overdue_count=len(overdue),
    )


_STATUS_PT = {
    "PAID": "paga",
    "PARTIAL": "parcial",
    "RENEGOTIATED": "renegociada",
    "IN_COLLECTION": "Em cobrança",
    "CANCELLED": "cancelada",
}


def status_label(status: str, due: date, today: date) -> str:
    """Rótulo ao aluno. A data de referência manda: todo status contado vencido é 'vencida'."""
    if status in COUNTED:
        if due < today:
            return "vencida"
        return "parcial" if status == "PARTIAL" else "aberta"
    return _STATUS_PT.get(status, status.lower())
