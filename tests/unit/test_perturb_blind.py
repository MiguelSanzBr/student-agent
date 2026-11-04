from pathlib import Path

import pytest

from evals.guard.perturb_blind import MUTATION_TEMPLATES, build_blind_set
from evals.oracle import load_csv


@pytest.fixture(scope="module")
def raw():
    return load_csv(Path(__file__).parents[1] / "fixtures" / "mini")


def test_deterministico(raw):
    assert build_blind_set(raw, seed=7) == build_blind_set(raw, seed=7)
    assert build_blind_set(raw, seed=7) != build_blind_set(raw, seed=8)


def test_mutacoes_mudam_o_texto(raw):
    muts = [i for i in build_blind_set(raw, seed=1) if i.expect_block]
    assert muts and all(i.mutated != i.original for i in muts)


def test_templates_representados(raw):
    items = build_blind_set(raw, seed=1)
    assert len(MUTATION_TEMPLATES) >= 12
    assert {i.template for i in items if i.expect_block} == set(MUTATION_TEMPLATES)


def test_controles(raw):
    items = build_blind_set(raw, seed=1)
    ctrl = [i for i in items if not i.expect_block]
    assert all(i.mutated == i.original for i in ctrl)
    assert len(ctrl) * 4 >= len(items) - len(ctrl)
