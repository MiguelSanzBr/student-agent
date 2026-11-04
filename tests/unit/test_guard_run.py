import json
from pathlib import Path

from core.ontology import Ontology
from evals.golden.build import build_items, ideal_turns, load_shared
from evals.golden.run import sample_utility  # noqa: F401
from evals.guard.run import cluster_ci, dev_report, main, render_md
from evals.oracle import load_csv

MINI = Path(__file__).parents[1] / "fixtures" / "mini"
ONTO = Ontology.load(Path("ontology"))


def test_cluster_ci_brackets_point():
    ci = cluster_ci({"a": [True] * 9 + [False], "b": [True] * 5, "c": [True, False] * 4},
                    n_boot=300, seed=1)  # fmt: skip
    assert ci.low <= ci.point <= ci.high


def test_guard_run_main_writes_files_and_refuses_second_run(tmp_path):
    args = ["--blind-seed", "5", "--data", str(MINI), "--out", str(tmp_path), "--n-boot", "100"]
    assert main(args) == 0
    g = json.loads((tmp_path / "guard.json").read_text())
    assert g["blind"]["n_mutated"] >= 100 and g["blind"]["recall"]["point"] > 0.5
    assert "Recall por template" in (tmp_path / "guard.md").read_text()
    assert main(args) == 2  # o cego é medido 1x


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
            "recall_by_template": {"t": w}}  # fmt: skip
