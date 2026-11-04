from datetime import date

from app.rules.finance import (
    Invoice,
    LateCharge,
    Payment,
    financial_position,
    invoice_balance,
    invoice_to_settle,
    open_charges,
)

TODAY = date(2026, 10, 31)


def inv(status="OVERDUE", net=10_000, due=date(2026, 9, 10), id="F1"):
    return Invoice(id, net, status, due)


def pay(principal, status="CONFIRMED", multa=0, juros=0, id="F1"):
    return Payment(id, principal, multa, juros, status)


def test_reversed():
    assert invoice_balance(inv(), [pay(10_000, "REVERSED")]) == 10_000


def test_partial_two_parts():
    assert invoice_balance(inv("PAID"), [pay(4_000), pay(6_000)]) == 0
    assert invoice_balance(inv("PARTIAL"), [pay(4_000), pay(6_000)]) == 0


def test_renegotiated_excluded():
    i = inv("RENEGOTIATED")
    assert invoice_balance(i, []) == 0 and invoice_to_settle(i, [], [LateCharge("F1", 500)]) == 0


def test_charges_already_paid():
    ch = [LateCharge("F1", 1_000), LateCharge("F1", 500, waived=True)]
    assert open_charges(inv(), [pay(0, multa=200, juros=300)], ch) == 500
    assert open_charges(inv(), [pay(0, "REVERSED", multa=200)], ch) == 1_000


def test_invoice_to_settle_is_balance_plus_open_charges():
    assert invoice_to_settle(inv(), [], [LateCharge("F1", 1_250)]) == 11_250


def test_position_reference_only():
    p = financial_position(
        [inv(), inv("OPEN", 5_000, date(2026, 11, 1), "F2")], [], [LateCharge("F1", 1_250)], TODAY
    )
    assert (p.overdue_principal, p.open_charges, p.total_due, p.overdue_count) == (
        10_000,
        1_250,
        11_250,
        1,
    )
    assert (p.upcoming, p.next_due_date, p.next_due_amount, p.days_to_next_due) == (
        5_000,
        date(2026, 11, 1),
        5_000,
        1,
    )


def test_overdue_with_principal_paid_but_charges_open_still_counts():
    p = financial_position([inv("OVERDUE")], [pay(10_000)], [LateCharge("F1", 500)], TODAY)
    assert p.overdue_count == 1 and p.open_charges == 500 and p.total_due == 500


def test_status_label_overdue_rule_for_every_counted_status():
    from app.rules.finance import status_label

    past, future = date(2026, 9, 10), date(2026, 11, 10)
    for st in ("OPEN", "OVERDUE", "PARTIAL"):
        assert status_label(st, past, TODAY) == "vencida"
    assert status_label("OPEN", future, TODAY) == "aberta"
    assert status_label("PARTIAL", future, TODAY) == "parcial"
    assert status_label("IN_COLLECTION", past, TODAY) == "Em cobrança"
    assert status_label("PAID", past, TODAY) == "paga"
