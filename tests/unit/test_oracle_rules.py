import random
from datetime import date
from pathlib import Path

import pytest

from evals import oracle as o


def test_rule_helpers() -> None:
    assert o.ra_check_digit("18172") == 2 and o.ra_valid("181722")
    assert not o.ra_valid("181723") and not o.ra_valid("18172")
    assert o.normalize_academic_ref("18172-2") == "181722"
    assert o.normalize_academic_ref("1253-0") == "012530"
    assert o.normalize_academic_ref(" 01233-5 ") == "012335"
    assert o.normalize_academic_ref("18015") == "180157"
    assert o.normalize_academic_ref("012815") == "012815"
    assert o.lpad("11924", 6) == "011924"
    assert o.cpf11("4145328485") == "04145328485"
    assert o.cpf11("081.405.226-60") == "08140522660"
    assert o.protocol_ref_digits("PRT-2022/0003006") == "20220003006"


def test_formats() -> None:
    assert o.money(4950843) == "R$ 49.508,43"
    assert o.money(5) == "R$ 0,05"
    assert o.br_date(date(2026, 12, 5)) == "05/12/2026"


def test_load_csv_accepts_directory(tmp_path: Path) -> None:
    (tmp_path / "sag").mkdir()
    (tmp_path / "sag" / "term.csv").write_text("TERM,IS_CURRENT\n2026/2,Y\n", encoding="utf-8")
    assert o.load_csv(tmp_path) == {"sag/term": [{"TERM": "2026/2", "IS_CURRENT": "Y"}]}


def test_mutate_one_fact_changes_exactly_one() -> None:
    ans = "Você deve R$ 10,00 até 05/11/2026."
    out = o.mutate_one_fact(ans, "money", random.Random(1))
    assert out != ans and "05/11/2026" in out and "R$ 10,00" not in out
    out = o.mutate_one_fact(ans, "date", random.Random(1))
    assert "R$ 10,00" in out and "05/11/2026" not in out


@pytest.mark.parametrize("kind", ["money", "date", "grade", "number"])
def test_mutate_every_kind_differs_and_keeps_others(kind: str) -> None:
    ans = "Você tem 3 faturas, R$ 99,12 até 05/11/2026 e média 8,5."
    for seed in range(20):
        out = o.mutate_one_fact(ans, kind, random.Random(seed))
        assert out != ans
        for other, frag in (("money", "R$ 99,12"), ("date", "05/11/2026"), ("grade", "8,5")):
            if other != kind:
                assert frag in out


def test_mutate_without_fact_raises() -> None:
    with pytest.raises(ValueError):
        o.mutate_one_fact("sem fatos", "money", random.Random(0))


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [("36.55", "36,6"), ("8.85", "8,9"), ("2.65", "2,7"), ("44.25", "44,3"), ("75", "75,0")],
)
def test_decimal1_rounds_half_up_from_raw_string(raw_value: str, expected: str) -> None:
    assert o.fmt_decimal1(raw_value) == expected
