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
    assert all(c["brand"] and c["ra"] for c in cases)
    assert all("cpf" not in c for c in cases)  # o caso é (marca, RA); o CPF vem dos dados


def test_fixture_composition() -> None:
    raw = read_raw(MINI)
    assert len(raw) == 47 and all(raw.values())
    enr = {(r["BRAND"], r["STUDENT_ID"]): r for r in raw["sag/enrollment"]}
    golden = {(c["brand"], c["ra"]) for c in _golden()}
    cpf = {r["PERSON_ID"]: r["CPF"] for r in raw["sag/person"]}  # só dígitos, mesma pessoa
    for c in _golden():  # outras matrículas do caso são da mesma pessoa (mesmo CPF)
        own = cpf[enr[(c["brand"], c["ra"])]["PERSON_ID"]]
        assert all(cpf[enr[tuple(k)]["PERSON_ID"]] == own for k in c["other_enrollments"])
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
    m = _script()
    real = m.real_cpfs(full)  # type: ignore[attr-defined]
    for t in CATALOGS:  # iguais ao original, exceto pelos CPFs pseudonimizados
        pseudo = [{k: m.pseudonymize(v, real) for k, v in r.items()} for r in full[t]]  # type: ignore[attr-defined]
        assert mini[t] == pseudo, t


def _script() -> object:
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "scripts/make_fixture.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _valid_cpf(c: str) -> bool:
    d = [int(x) for x in c]
    for n in (9, 10):
        if (
            sum(v * w for v, w in zip(d[:n], range(n + 1, 1, -1), strict=True)) * 10
        ) % 11 % 10 != d[n]:
            return False
    return len(c) == 11 and len(set(c)) > 1


def test_fake_cpf_is_valid_deterministic_and_keeps_format() -> None:
    m = _script()
    fake = m.fake_cpf  # type: ignore[attr-defined]
    out = fake("52998224725")
    assert _valid_cpf(out) and out != "52998224725" and out == fake("52998224725")
    dotted = fake("529.982.247-25")
    assert dotted.replace(".", "").replace("-", "") == out and dotted.count(".") == 2
    assert fake("052998224725"[1:]) == out.lstrip("0")  # coluna sem zero à esquerda segue sem zero
