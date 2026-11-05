import json
import re
import shutil
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
    # teste cego: sensibilidades ao lado do primário (validação do scorer e ferramenta-domínio)
    assert n["quality.test.corrected"].startswith("77,1% [")
    assert n["quality.test.domain_tools"].startswith("75,8% [")
    assert n["quality.test.guard_blocked_ok"] == "124 de 3321 itens (3,7%)"
    assert all(isinstance(v, str) for v in n.values())


def _real_evidence(d: Path) -> None:
    """Arquivos no formato REAL dos runners, gerados pelas funções de resumo deles."""
    import random

    from evals.golden.run import Outcome, RoundResult
    from evals.golden.run import summarize as golden
    from evals.guard.run import _d, _w
    from evals.latency.bench import Sample
    from evals.latency.bench import summarize as latency
    from evals.redteam.check import Attack
    from evals.redteam.run import Result
    from evals.redteam.run import summarize as redteam
    from evals.stats import Label, wilson

    for tag, split in (
        ("dev-r1", "dev"),
        ("dev-r2", "dev"),
        ("dev-r3", "dev"),
        ("dev-r4", "dev"),
        ("dev-r5", "dev"),
        ("test-r1", "test"),
        ("test-r2", "test"),  # secundárias: chaves quality.test.rN.*; o primário segue sendo a r1
        ("test-r3", "test"),
        ("test-r4", "test"),
    ):
        outs = [
            Outcome(
                f"t{n}@s{n % 4}",
                f"s{n % 4}",
                f"t{n}",
                "academico" if n % 2 else "cruzamento",
                "shared" if n % 3 else "test_only",
                "pass" if n % 5 else "fail",
                1,
                expect_refusal=n % 4 == 0,
                refused_unduly=n % 7 == 0,
            )
            for n in range(40)
        ]
        q = golden(
            RoundResult(outs),
            strata={f"s{i}": "x" for i in range(4)},
            n_boot=50,
            meta={"split": split, "round": int(tag[-1]), "primary_run": tag == "test-r1"},
            labels=[Label(i % 2 == 0, i % 3 != 0) for i in range(12)] if split == "test" else (),
        )
        (d / "quality" / tag).mkdir(parents=True)
        (d / "quality" / tag / "quality.json").write_text(json.dumps(q))
        if split == "test":  # sensibilidade: --resummarize com os rótulos da própria rodada
            (d / "quality" / tag / "quality.resummarized.json").write_text(json.dumps(q))
        if tag == "test-r1":  # ferramenta-domínio só no primário
            (d / "quality" / tag / "ferramenta-dominio").mkdir()
            (d / "quality" / tag / "ferramenta-dominio" / "quality.json").write_text(json.dumps(q))
    rs = [
        Result(Attack(f"a#{i}", 1 + 7 * (i > 2), f"t{i}", ("x",)), 0, i == 0, i == 4, ())
        for i in range(6)
    ]
    sec = redteam(rs, random.Random(0)) | {
        "holdout": {"behavior": redteam(rs, random.Random(0))["behavior"]}
    }
    (d / "security.json").write_text(json.dumps(sec))
    weak = {t: {"k": 0, "n": 10} for t in ("status_word", "flip_negation")}
    g = {
        "blind": {
            "recall": _d(wilson(8, 10)),
            "false_block": _w(0, 10),
            "recall_by_template": weak,
        }
    }
    (d / "quality" / "guard").mkdir()  # `make eval-guard`: EVIDENCE_DIR=docs/evidence/quality
    (d / "quality" / "guard" / "guard.json").write_text(json.dumps(g))
    xs = [
        Sample(
            f"s{i % 10}", c, 3.0 + i / 100, 1.0, {"overhead": 0.2}, cost_usd=0.002, input_tokens=100
        )
        for c in ("card", "1", "2+")
        for i in range(30)
    ]
    (d / "latency-A.json").write_text(json.dumps({"window": "A", **latency(xs, seed=1, n_boot=50)}))
    (d / "load-a-w1.json").write_text(
        json.dumps(
            {"ladder": "a", "pass": True, "steps": [{"rate": 1, "ok": 10}, {"rate": 2, "ok": 18}]}
        )
    )


def test_build_numbers_maps_real_runner_outputs(tmp_path: Path) -> None:
    (tmp_path / "quality").mkdir()
    _real_evidence(tmp_path)
    n = build_numbers(tmp_path)
    marked = set()
    for doc in DOCS:
        marked |= {k for k in re.findall(r"<!-- num:([\w.\-]+) -->", doc.read_text())}
    assert {
        k for k in marked if k.split(".")[0] in {"quality", "security", "guard", "latency"}
    } <= set(n)
    assert n["security.holdout.behavior.success"].startswith("1/3 templates")
    assert n["latency.load.plateau"].startswith("escada (a) aprovada: 18")
    assert n["latency.two_plus.p95"].endswith(" s")


def test_publish_evidence_closes_the_cycle(tmp_path: Path) -> None:
    import shutil

    from scripts.publish_evidence import publish

    ev, out = tmp_path / "evidence", tmp_path / "eval-out"
    (ev / "quality").mkdir(parents=True)
    out.mkdir()
    _real_evidence(ev)
    for n in ("security.json", "latency-A.json", "load-a-w1.json"):  # saem dos runners no eval-out
        shutil.move(ev / n, out / n)
    (out / "security.md").write_text("# seg")
    sensitive = (
        "results.jsonl results_blind.jsonl label_sample.jsonl label_sample_blind.jsonl "
        "forbidden_map.json forbidden_map_blind.json transcripts.jsonl golden_login.json "
        "security_blind.md load-b-w1.json"
    ).split()
    for n in sensitive:
        (out / n).write_text("segredo")
    (out / "quality").mkdir()
    (out / "quality" / "transcripts.jsonl").write_text("segredo")
    publish(out, ev)
    assert sorted(p.name for p in ev.iterdir() if p.is_file()) == [
        "latency-A.json",
        "load-a-w1.json",
        "security.json",
        "security.md",
    ]
    assert not list(ev.rglob("*.jsonl")) and not (ev / "forbidden_map.json").exists()
    assert (ev / "security.json").stat().st_mode & 0o444 == 0o444
    for n in ("data-quality.json", "spikes.json"):  # versionados, não vêm dos runners
        shutil.copyfile(EVIDENCE / n, ev / n)
    n = build_numbers(ev)
    docs = []
    for d in DOCS:
        c = tmp_path / d.name
        c.write_text(d.read_text())
        docs.append(c)
    render_docs(docs, n)  # KeyError = chave de runner sem número
    assert "guard.blind_recall" in n and "latency.load.plateau" in n


def test_build_numbers_uses_highest_non_rehearsal_round(tmp_path: Path) -> None:
    for r, point in ((1, 0.5), (2, 0.9), (3, 0.1)):
        f = tmp_path / "quality" / f"dev-r{r}" / "quality.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"primary": {"point": point}, "rehearsal": r == 3}))
    assert build_numbers(tmp_path)["quality.dev.pass1"] == "90,0% (IC indisponível)"


def test_build_numbers_without_runner_outputs_has_no_runner_keys(tmp_path: Path) -> None:
    shutil.copyfile(EVIDENCE / "data-quality.json", tmp_path / "data-quality.json")
    assert not any(
        k.startswith(("quality.", "security.", "guard.", "latency."))
        for k in build_numbers(tmp_path)
    )


def test_build_numbers_with_latency_a_has_latency_keys(tmp_path: Path) -> None:
    shutil.copyfile(EVIDENCE / "latency-A.json", tmp_path / "latency-A.json")
    assert any(k.startswith("latency.") for k in build_numbers(tmp_path))


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


def test_build_numbers_refuses_simulated_latency(tmp_path: Path) -> None:
    (tmp_path / "quality").mkdir()
    _real_evidence(tmp_path)
    p = tmp_path / "latency-A.json"
    p.write_text(json.dumps({**json.loads(p.read_text()), "simulated": True}))
    with pytest.raises(ValueError, match="simulated"):
        build_numbers(tmp_path)


def test_allow_missing_keeps_old_content_and_lists_missing(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("<!-- num:ok -->?<!-- /num --> <!-- num:falta -->a medir<!-- /num -->")
    missing = render_docs([f], {"ok": "1"}, allow_missing=True)
    assert missing == ["falta"]
    assert f.read_text() == "<!-- num:ok -->1<!-- /num --> <!-- num:falta -->a medir<!-- /num -->"


def test_strict_is_default_and_returns_no_missing(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("<!-- num:ok -->?<!-- /num -->")
    assert render_docs([f], {"ok": "1"}) == []


def test_build_numbers_has_one_key_per_dev_round(tmp_path: Path) -> None:
    for r, point in ((1, 0.5), (2, 0.6), (3, 0.7)):
        f = tmp_path / "quality" / f"dev-r{r}" / "quality.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"primary": {"point": point}, "rehearsal": False}))
    n = build_numbers(tmp_path)
    assert [n[f"quality.dev.r{r}.pass1"] for r in (1, 2, 3)] == [
        "50,0% (IC indisponível)",
        "60,0% (IC indisponível)",
        "70,0% (IC indisponível)",
    ]
    assert n["quality.dev.pass1"] == n["quality.dev.r3.pass1"]


def test_build_numbers_has_guard_weak_templates(tmp_path: Path) -> None:
    (tmp_path / "quality" / "guard").mkdir(parents=True)
    rec = {k: {"k": 0, "n": 10} for k in ("status_word", "flip_negation")}
    rec["flip_negation"]["k"] = 7
    g = {
        "blind": {
            "recall": {"point": 0.9},
            "false_block": {"k": 0, "n": 43, "point": 0.0},
            "recall_by_template": rec,
        }
    }
    (tmp_path / "quality" / "guard" / "guard.json").write_text(json.dumps(g))
    n = build_numbers(tmp_path)
    assert (n["guard.weak.status_word"], n["guard.weak.flip_negation"]) == ("0/10", "7/10")
