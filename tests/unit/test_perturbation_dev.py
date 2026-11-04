from pathlib import Path

from core.ontology import Ontology
from evals.guard.perturb_dev import build_dev_set, score

ONTO = Ontology.load(Path("ontology"))


def test_perturbation_dev_recall_fpr():
    res = score(build_dev_set(40, 7), ONTO)
    for kind in ("money", "date", "id"):
        assert res.total[kind] >= 40 and res.recall(kind) >= 0.95, (kind, res)
    assert res.fpr <= 0.05, res
    for kind in ("action", "polarity", "url", "opaque", "commitment", "status", "period",
                 "pct", "int", "email"):  # fmt: skip
        assert res.recall(kind) >= 0.9, (kind, res)


def test_dev_set_has_controls_and_every_kind():
    items = build_dev_set(5, 1)
    assert {i.kind for i in items} >= {"money", "date", "id", "control", "url", "action"}
    assert not any(i.expect_block for i in items if i.kind == "control")
