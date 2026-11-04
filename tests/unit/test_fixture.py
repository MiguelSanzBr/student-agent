import json
from pathlib import Path

import pytest

from etl.raw import read_raw

ROOT = Path(__file__).parents[2]
MINI = ROOT / "tests/fixtures/mini"
GOLDEN = ROOT / "tests/fixtures/golden.json"
ZIP = ROOT / "data/dados.zip"
CATALOGS = [
    "sag/brand", "sag/unit", "sag/course", "sag/curriculum", "sag/curriculum_subject",
    "sag/subject", "sag/course_offer", "sag/term", "sag/professor", "secretary/demand_type",
    "secretary/unit", "finance/company", "finance/product", "finance/price_list",
    "lms/mdl_tenant", "lms/mdl_course_categories",
]  # fmt: skip


def _golden() -> list[dict[str, str]]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["cases"]


def test_golden_json_has_10_cases() -> None:
    cases = _golden()
    assert [c["case"] for c in cases] == list(range(1, 11))
    assert all(c["brand"] and c["ra"] and c["cpf"] for c in cases)


def test_fixture_composition() -> None:
    raw = read_raw(MINI)
    assert len(raw) == 47 and all(raw.values())
    enr = {(r["BRAND"], r["STUDENT_ID"]): r for r in raw["sag/enrollment"]}
    cpf = {r["PERSON_ID"]: r["CPF"].replace(".", "").replace("-", "") for r in raw["sag/person"]}
    golden = {(c["brand"], c["ra"]) for c in _golden()}
    for c in _golden():
        e = enr[(c["brand"], c["ra"])]
        assert cpf[e["PERSON_ID"]] == c["cpf"]
    partners = {k for k in enr if k[1] in {ra for _, ra in golden}} - golden
    assert len(partners) == 11
    assert ("UniSaoPaulo", "180548") in enr  # irmão do golden 4
    assert enr[("UniAraucaria", "090948")]["ENROLLMENT_STATUS"] == "LOCKED"
    third = [r for r in raw["secretary/protocol"] if r["Requester_Type"] == "Third Party"]
    assert len(third) == 1
    assert any(r["Requester_Type"] == "Guardian" for r in raw["secretary/protocol"])
    assert sum(f.stat().st_size for f in MINI.rglob("*.csv")) < 3_000_000


@pytest.mark.skipif(not ZIP.exists(), reason="data/dados.zip ausente (CI sem dados)")
def test_fixture_catalogs_complete() -> None:
    full, mini = read_raw(ZIP), read_raw(MINI)
    for t in CATALOGS:
        assert mini[t] == full[t], t
