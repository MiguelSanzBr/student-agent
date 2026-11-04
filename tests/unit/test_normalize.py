from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from core.format import fmt_date, fmt_grade, fmt_instant, fmt_pct
from core.keys import brand_family
from core.normalize import (
    lms_login,
    normalize_academic_ref,
    normalize_cpf,
    normalize_ra,
    parse_bool,
    parse_brl,
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


def test_normalize_ra_requires_valid_digit() -> None:
    assert normalize_ra("18001-7") == "180017"
    assert normalize_ra("18001-8") is None
    assert normalize_ra("1253-0") is None  # RA canônico tem 5 dígitos na base


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


def test_money_cpf_family_bool() -> None:
    assert parse_brl("R$ 1.234,56") == 123456
    assert normalize_cpf("8140522660") == "08140522660"
    assert brand_family("UniSaoPaulo_EAD") == "UniSaoPaulo"
    assert parse_bool("Y") and parse_bool("True") and parse_bool("1") and not parse_bool("N")


def test_parse_naive_local_is_sao_paulo() -> None:
    dt = parse_naive_local("2026-11-01 02:01")
    assert dt == datetime(2026, 11, 1, 2, 1, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert fmt_instant(dt) == "01/11/2026 02:01"


def test_fmt_helpers_edges() -> None:
    assert fmt_date(date(2026, 11, 5)) == "05/11/2026"
    assert fmt_grade(Decimal("7.25")) == "7,3"
    assert fmt_grade(Decimal("10")) == "10,0"
    assert fmt_pct(Decimal("75")) == "75,0%"
    with pytest.raises(ValueError):
        fmt_instant(datetime(2026, 11, 1, 2, 1))  # datetime ingênuo é ambíguo
