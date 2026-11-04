import json
import re
from pathlib import Path

import pytest

from scripts.render_docs import build_numbers, find_hand_typed, render_docs

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence"
DOCS = [ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md", EVIDENCE / "README.md"]
COMMENT = re.compile(r"<!--.*?-->", re.S)


def test_render_replaces_all_markers(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("x <!-- num:a.b -->?<!-- /num --> y <!-- num:c -->?<!-- /num -->\n")
    render_docs([f], {"a.b": "1,5", "c": "7"})
    assert f.read_text() == "x <!-- num:a.b -->1,5<!-- /num --> y <!-- num:c -->7<!-- /num -->\n"
    render_docs([f], {"a.b": "2", "c": "7"})  # idempotente: os marcadores sobrevivem
    assert "<!-- num:a.b -->2<!-- /num -->" in f.read_text()


def test_render_fails_on_missing_key(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("<!-- num:ok -->?<!-- /num --> <!-- num:falta -->?<!-- /num -->")
    before = f.read_text()
    with pytest.raises(KeyError, match="falta"):
        render_docs([f], {"ok": "1"})
    assert f.read_text() == before  # nada é gravado pela metade


def test_build_numbers_from_real_evidence() -> None:
    n = build_numbers(EVIDENCE)
    assert n["data.protocols_linked"] == "3911"
    assert n["data.rule.ra_cpf"] == "3455"
    assert n["data.lms_accounts"] == "1003"
    assert n["a2a.TASK_NOT_FOUND"] == "-32001"
    assert all(isinstance(v, str) for v in n.values())


def test_build_numbers_flattens_later_evidence_and_tolerates_absence(tmp_path: Path) -> None:
    assert not any(k.startswith("quality.") for k in build_numbers(EVIDENCE))
    (tmp_path / "quality.json").write_text(json.dumps({"test": {"pass1": 0.9, "n": 40}}))
    n = build_numbers(tmp_path)
    assert n == {"quality.test.pass1": "0.9", "quality.test.n": "40"}


def test_find_hand_typed_detects_only_unmarked_values() -> None:
    nums = {"data.x": "20277", "data.y": "0", "a2a.z": "-32001"}
    ok = "<!-- num:data.x -->20277<!-- /num --> e <!-- num:a2a.z -->-32001<!-- /num -->"
    assert find_hand_typed(ok, nums) == []
    assert find_hand_typed("sao 20.277 linhas", nums) == ["20.277"]
    assert find_hand_typed("erro -32001 e 0 itens", nums) == ["-32001"]  # "0" é curto demais
    assert find_hand_typed("<!-- rascunho: 20277 -->", nums) == []


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_no_hand_typed_numbers(doc: Path) -> None:
    assert find_hand_typed(doc.read_text(), build_numbers(EVIDENCE)) == []


def test_readme_fits_one_screen() -> None:
    assert len((ROOT / "README.md").read_text().splitlines()) <= 50


def test_architecture_page_budget() -> None:
    text = COMMENT.sub("", (ROOT / "docs" / "ARCHITECTURE.md").read_text())
    body = text.split("\n## Apêndice")[0]
    assert len(body.split()) <= 6000


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_docs_have_no_em_dash(doc: Path) -> None:
    assert "—" not in doc.read_text()


def _fixture_checks(**legs_by_name: dict[str, object]) -> dict[str, object]:
    names = {
        "pessoas": 5,
        "matrículas": 6,
        "contratos resolvidos": 7,
        "protocolos ligados": 80,
        "protocolos sem vínculo": 2,
        "contas LMS ligadas": 9,
        "tentativas do LMS": 10,
        "simulados abertos sem tentativa": 11,
        "simulados abertos com tentativa restante": 12,
        "provas com nota encerradas sem tentativa": 0,
        "logins de analista ausentes": 13,
    }
    checks = [{"name": n, "legs": {"sql": v, "oraculo": v}, "ok": True} for n, v in names.items()]
    pair = [4, 4]
    checks += [
        {"name": "PROTOCOL_REF", "legs": {"memoria": pair, "sql": pair}, "ok": True},
        {"name": "snapshot do último mês", "legs": {"sql": pair, "oraculo": pair}, "ok": True},
        {"name": "protocolos por regra", "legs": {"memoria": {"ra_cpf": 3}}, "ok": True},
    ]
    for c in checks:
        if c["name"] in legs_by_name:
            c["legs"] = legs_by_name[c["name"]]  # type: ignore[index]
    return {"checks": checks}


def test_build_numbers_reads_leg_schema_and_rejects_disagreement(tmp_path: Path) -> None:
    (tmp_path / "data-quality.json").write_text(json.dumps(_fixture_checks()))
    n = build_numbers(tmp_path)
    assert n["data.persons"] == "5" and n["data.snapshot"] == "4" and n["data.rule.ra_cpf"] == "3"
    (tmp_path / "data-quality.json").write_text(
        json.dumps(_fixture_checks(pessoas={"sql": 5, "oraculo": 6}))
    )
    with pytest.raises(ValueError, match="pessoas"):
        build_numbers(tmp_path)
