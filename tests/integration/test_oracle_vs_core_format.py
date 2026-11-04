from decimal import Decimal
from pathlib import Path

import pytest

from core.format import fmt_grade, fmt_pct
from evals import oracle as o

pytestmark = pytest.mark.fulldata
ZIP = Path(__file__).resolve().parents[2] / "data" / "dados.zip"


def test_oracle_decimal_format_equals_core_over_all_enrollments() -> None:
    if not ZIP.exists():
        pytest.skip("data/dados.zip ausente")
    raw = o.load_csv(ZIP)
    n = 0
    for e in raw["sag/enrollment"]:
        pct = e["CURRENT_CURRICULUM_COMPLETION"]
        assert o.fmt_decimal1(pct) + "%" == fmt_pct(Decimal(pct))
        if e["GPA"]:
            assert o.fmt_decimal1(e["GPA"]) == fmt_grade(Decimal(e["GPA"]))
        n += 1
    assert n == 1037
