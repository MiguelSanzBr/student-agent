from datetime import UTC, date, datetime

from core.format import fmt_brl, fmt_date, fmt_instant, fmt_period
from core.normalize import parse_epoch


def test_dates() -> None:
    assert fmt_date(date(2026, 11, 5)) == "05/11/2026"
    assert fmt_instant(datetime(2026, 11, 1, 2, 1, tzinfo=UTC)) == "31/10/2026 23:01"
    assert parse_epoch("0") is None and fmt_period("2026/2") == "2026/2"


def test_fmt_brl() -> None:
    assert fmt_brl(4950843) == "R$ 49.508,43"
    assert fmt_brl(5) == "R$ 0,05"
    assert fmt_brl(-150) == "-R$ 1,50"
