from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.format import fmt_date, fmt_grade, fmt_instant, fmt_pct
from core.keys import brand_family
from core.normalize import (
    digits,
    lms_login,
    normalize_academic_ref,
    normalize_cpf,
    normalize_ra,
    parse_bool,
    parse_brl,
    parse_date,
    parse_iso,
    parse_naive_local,
    ra_check_digit,
)


@pytest.mark.parametrize(
    "base,dv", [("18001", "7"), ("18015", "7"), ("01253", "0"), ("18172", "2")]
)
def test_ra_check_digit(base: str, dv: str) -> None:
    assert ra_check_digit(base) == dv


@pytest.mark.parametrize(
    "raw,ra",
    [
        ("18001-7", "180017"),
        ("1253-0", "012530"),
        ("18015", "180157"),
        (" 01281-5 ", "012815"),
        ("012816", None),
        ("", None),
    ],
)
def test_normalize_academic_ref(raw: str, ra: str | None) -> None:
    assert normalize_academic_ref(raw) == ra


@pytest.mark.parametrize(
    "raw,ra",
    [
        ("18001-7", "180017"),
        ("180017", "180017"),
        ("18001-8", None),  # DV errado
        ("1253-0", None),  # RA canônico tem 5 dígitos na base
        ("", None),
    ],
)
def test_normalize_ra(raw: str, ra: str | None) -> None:
    assert normalize_ra(raw) == ra


@pytest.mark.parametrize(
    "raw,login",
    [
        (" EAD_01382", "EAD_01382"),
        ("ead_01206", "EAD_01206"),
        ("prof40013", None),
        ("aluno.teste3", None),
    ],
)
def test_lms_login(raw: str, login: str | None) -> None:
    assert lms_login(raw) == login


@pytest.mark.parametrize(
    "raw,cpf",
    [("8140522660", "08140522660"), ("081.405.226-60", "08140522660"), ("", None)],
)
def test_normalize_cpf(raw: str, cpf: str | None) -> None:
    assert normalize_cpf(raw) == cpf


@pytest.mark.parametrize(
    "raw,expected",
    [("(11) 98765-4321", "11987654321"), ("CPF 081.405.226-60", "08140522660"), ("abc", "")],
)
def test_digits(raw: str, expected: str) -> None:
    assert digits(raw) == expected


@pytest.mark.parametrize(
    "raw,cents",
    [("R$ 1.234,56", 123456), ("1.234,56", 123456), ("-R$ 0,05", -5), ("R$ 1,234", None)],
)
def test_parse_brl(raw: str, cents: int | None) -> None:
    if cents is None:
        with pytest.raises(ValueError):
            parse_brl(raw)
    else:
        assert parse_brl(raw) == cents


@pytest.mark.parametrize("raw,value", [("Y", True), ("True", True), ("1", True), ("N", False)])
def test_parse_bool(raw: str, value: bool) -> None:
    assert parse_bool(raw) is value


@pytest.mark.parametrize(
    "raw,fmt,expected",
    [("05/11/2026", "%d/%m/%Y", date(2026, 11, 5)), ("2026-11-01", "%Y-%m-%d", date(2026, 11, 1))],
)
def test_parse_date(raw: str, fmt: str, expected: date) -> None:
    assert parse_date(raw, fmt) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-11-01T02:01:00+00:00", datetime(2026, 11, 1, 2, 1, tzinfo=UTC)),
        (
            "2026-11-01T02:01:00-03:00",
            datetime(2026, 11, 1, 2, 1, tzinfo=timezone(timedelta(hours=-3))),
        ),
    ],
)
def test_parse_iso(raw: str, expected: datetime) -> None:
    assert parse_iso(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-11-01 02:01", datetime(2026, 11, 1, 2, 1, tzinfo=ZoneInfo("America/Sao_Paulo"))),
    ],
)
def test_parse_naive_local(raw: str, expected: datetime) -> None:
    dt = parse_naive_local(raw)
    assert dt == expected
    assert fmt_instant(dt) == "01/11/2026 02:01"
    with pytest.raises(ValueError):
        parse_naive_local("2026-11-01T02:01:00+00:00")  # já tem fuso


def test_money_cpf_family_bool() -> None:
    assert brand_family("UniSaoPaulo_EAD") == "UniSaoPaulo"


@pytest.mark.parametrize("raw,expected", [("7.25", "7,3"), ("10", "10,0"), ("9.95", "10,0")])
def test_fmt_grade(raw: str, expected: str) -> None:
    assert fmt_grade(Decimal(raw)) == expected


@pytest.mark.parametrize("raw,expected", [("75", "75,0%"), ("12.345", "12,3%")])
def test_fmt_pct(raw: str, expected: str) -> None:
    assert fmt_pct(Decimal(raw)) == expected


def test_fmt_date_and_naive_instant() -> None:
    assert fmt_date(date(2026, 11, 5)) == "05/11/2026"
    with pytest.raises(ValueError):
        fmt_instant(datetime(2026, 11, 1, 2, 1))  # datetime ingênuo é ambíguo
