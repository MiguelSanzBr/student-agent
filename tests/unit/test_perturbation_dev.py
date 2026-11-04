from pathlib import Path

from core.ontology import Ontology
from evals.guard.perturb_dev import AUTHORED, build_dev_set, score

ONTO = Ontology.load(Path("ontology"))
HERE = Path(__file__).parent


def test_perturbation_dev_recall_fpr():
    res = score(build_dev_set(40, 7), ONTO)
    print(res)
    for kind in ("money", "date", "id"):
        assert res.total[kind] >= 40 and res.recall(kind) >= 0.95, (kind, res)
    assert res.fpr <= 0.05, res
    for group in ("control:oracle_empty", "control:cross", "control:question"):
        assert res.fpr_of(group) <= 0.05, (group, res)
    # medido sem ajuste às frases retidas: "está aberto" vira ação (limite conhecido, ver relatório)
    assert res.fpr_of("control:empty") <= 0.25, res
    assert res.recall("polarity") >= 0.85 and res.recall("commitment") >= 0.7, res
    for kind in ("swap:date", "swap:pct", "swap:decimal", "swap:money"):
        assert res.recall(kind) >= 0.95, (kind, res)


def test_dev_set_has_controls_and_every_kind():
    items = build_dev_set(5, 1)
    assert {i.kind for i in items} >= {"money", "date", "id", "control", "url", "action"}
    assert {i.kind for i in items} >= {"control:empty", "control:oracle_empty", "swap:date"}
    assert any(i.question for i in items)
    assert not any(i.expect_block for i in items if i.kind.startswith("control"))


def test_authored_strings_are_held_out_from_unit_tests():
    unit = "".join(p.read_text() for p in HERE.glob("test_*guard*.py"))
    assert AUTHORED and not [s for s in AUTHORED if s in unit]
