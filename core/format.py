"""Formatação para exibição ao aluno, em PT-BR."""

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from core.normalize import SP


def fmt_brl(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    reais, centavos = divmod(abs(cents), 100)
    return f"{sign}R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def fmt_instant(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime sem fuso é ambíguo")
    return dt.astimezone(SP).strftime("%d/%m/%Y %H:%M")


def _one_decimal(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")


def fmt_grade(x: Decimal) -> str:
    return _one_decimal(x)


def fmt_pct(x: Decimal) -> str:
    """x já em percentual (75 => '75,0%')."""
    return _one_decimal(x) + "%"


def fmt_period(term: str) -> str:
    return term
