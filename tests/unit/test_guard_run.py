import json
from pathlib import Path

import pytest

from core.ontology import Ontology
from evals.golden.build import build_items, ideal_turns, load_shared
from evals.guard.run import cluster_ci, dev_report, main, render_md
from evals.oracle import load_csv

MINI = Path(__file__).parents[1] / "fixtures" / "mini"
ONTO = Ontology.load(Path("ontology"))


def test_cluster_ci_brackets_point():
    ci = cluster_ci({"a": [True] * 9 + [False], "b": [True] * 5, "c": [True, False] * 4},
                    n_boot=300, seed=1)  # fmt: skip
    assert ci.low <= ci.point <= ci.high


def test_guard_run_main_mechanics_with_fake_guard(tmp_path, monkeypatch):
    """CI só valida a mecânica: o guard real NUNCA é medido contra o conjunto cego aqui."""
    import evals.guard.run as gr

    monkeypatch.setattr(gr, "blocked", lambda answer, fn, enr, raw, onto: len(answer) % 2 == 0)
    monkeypatch.setenv("EVAL_OUT_DIR", str(tmp_path / "eval-out"))
    args = ["--data", str(MINI), "--out", str(tmp_path / "o1"), "--n-boot", "100"]
    assert main(args) == 0
    g = json.loads((tmp_path / "o1" / "guard.json").read_text())
    assert g["blind"]["seed"] == gr.BLIND_SEED and g["blind"]["n_mutated"] >= 100
    assert "Recall por template" in (tmp_path / "o1" / "guard.md").read_text()
    assert (tmp_path / "eval-out/guard/.blind_measured").exists()
    # 1x só, mesmo com outro --out
    assert main([*args[:2], "--out", str(tmp_path / "o2"), "--n-boot", "100"]) == 2
    assert not (tmp_path / "o2").exists()


def test_blind_seed_is_a_constant_not_an_argument():
    import evals.guard.run as gr

    assert isinstance(gr.BLIND_SEED, int)
    with pytest.raises(SystemExit):
        main(["--blind-seed", "1"])


def test_empty_blind_templates_are_listed(monkeypatch):
    import evals.guard.run as gr

    monkeypatch.setattr(gr, "blocked", lambda *a: True)
    real = gr.build_blind_set
    monkeypatch.setattr(gr, "build_blind_set", lambda raw, seed: [
        i for i in real(raw, seed=seed) if i.template != "money_digit"])  # fmt: skip
    b = gr.blind_report(load_csv(MINI), ONTO, seed=1, n_boot=20)
    assert b["empty_templates"] == ["money_digit"]


def test_dev_report_crosstab():
    raw = load_csv(MINI)
    enr = {"s1": next(iter(sorted((r["BRAND"], r["STUDENT_ID"]) for r in raw["sag/enrollment"])))}
    items = {i.id: i for i in build_items(load_shared(), [], enr, raw)}
    rows = []
    for it in items.values():
        (t,) = ideal_turns(it, raw)
        rows.append({"item_id": it.id, "turns": [{"q": "", "a": t.answer, "pre_guard": None}]})
    d = dev_report(rows, items, raw, ONTO, seed=3)
    assert d["perturbados"] > 0 and sum(d["cruzamento"].values()) == d["perturbados"]
    assert d["rascunhos_certos"]["n"] > 0
    assert "Rascunhos do dev" in render_md({"blind": _blind(), "dev": d})


def _blind():
    w = {"k": 0, "n": 1, "point": 0.0, "low": 0.0, "high": 1.0}
    return {"seed": 1, "n_mutated": 1, "n_controls": 1, "recall": w, "false_block": w,
            "recall_by_template": {"t": w}, "empty_templates": []}  # fmt: skip
