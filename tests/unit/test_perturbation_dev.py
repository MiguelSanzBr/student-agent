from pathlib import Path

from core.ontology import Ontology
from evals.guard.perturb_dev import (
    _SWAPS,
    AUTHORED,
    QUESTION_STRINGS,
    build_dev_set,
    score,
)

ONTO = Ontology.load(Path("ontology"))
HERE = Path(__file__).parent


def test_perturbation_dev_recall_fpr():
    res = score(build_dev_set(40, 7), ONTO)
    print("\n" + res.report())
    for kind in ("money", "date", "id"):
        s = res[f"oracle/{kind}"]
        assert s.n_block >= 20 and s.recall >= 0.95, (kind, s)
    assert res["oracle"].fpr <= 0.05


def test_dev_set_has_every_group_with_enough_distinct_strings():
    res = score(build_dev_set(40, 7), ONTO)
    for g in ("authored:negations", "authored:commitments", "authored:actions", "authored:status",
              "authored:leaks", "authored:empty", "authored:controls", "swap", "question"):  # fmt: skip
        s = res[g]
        assert s.n_block + s.n_ctl >= 8, (g, s)


def test_authored_strings_are_held_out_from_unit_tests():
    # glob de TODOS os testes unitários; cobre frases, moldes de troca, perguntas e ids de registro
    unit = "".join(p.read_text() for p in HERE.glob("*.py") if p.name != Path(__file__).name)
    held = [*AUTHORED, *(t[2] for t in _SWAPS), *QUESTION_STRINGS]
    leaked = [s for s in held if f'"{s}"' in unit or (len(s) >= 25 and s in unit)]
    assert AUTHORED and not leaked, leaked
