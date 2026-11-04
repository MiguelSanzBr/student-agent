import random
from statistics import NormalDist

import pytest

from evals.stats import (
    CI,
    ItemResult,
    Label,
    cohen_kappa,
    corrected_pass,
    corrected_pass_ci,
    p_target,
    ppv_npv,
    two_way_bootstrap_ci,
    wilson,
)


def grid(p, icc, n_s, n_t, rng):
    """Grade aluno x template com efeitos aleatórios (probit); E[passou] = p."""
    thr = NormalDist().inv_cdf(p)
    a = [rng.gauss(0, 1) for _ in range(n_s)]
    b = [rng.gauss(0, 1) for _ in range(n_t)]
    w = (icc / 2) ** 0.5
    return [
        ItemResult(f"{s}-{t}", f"s{s}", f"t{t}",
                   w * a[s] + w * b[t] + (1 - icc) ** 0.5 * rng.gauss(0, 1) < thr)
        for s in range(n_s) for t in range(n_t)
    ]  # fmt: skip


def test_wilson_known_values():
    ci = wilson(0, 40)
    assert ci.low == 0.0 and ci.high == pytest.approx(0.088, abs=0.001)
    assert wilson(20, 40).point == 0.5
    assert wilson(0, 0) == CI(0.0, 1.0, 0.0)


def test_corrected_pass_formula():
    assert corrected_pass(0.9, 0.95, 0.8) == 0.9 * 0.95 + 0.1 * 0.2


def test_cohen_kappa_known():
    assert cohen_kappa([True, True, False, False], [True, True, False, False]) == 1.0
    a = [True] * 20 + [False] * 5 + [True] * 10 + [False] * 15  # 50 itens
    b = [True] * 20 + [True] * 5 + [False] * 10 + [False] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4, abs=1e-9)
    assert cohen_kappa([True, True], [True, True]) == 1.0  # sem variância: acordo total


def test_ppv_npv_counts():
    labels = ([Label(True, True)] * 18 + [Label(True, False)] * 2
              + [Label(False, False)] * 4 + [Label(False, True)] * 2)  # fmt: skip
    vpp, vpn = ppv_npv(labels)
    assert (vpp.point, vpn.point) == (0.9, 4 / 6)
    assert vpp.low < 0.9 < vpp.high


def test_bootstrap_deterministic_and_contains_point():
    res = grid(0.9, 0.3, 20, 10, random.Random(1))
    strata = {f"s{s}": "a" if s % 2 else "b" for s in range(20)}
    c1 = two_way_bootstrap_ci(res, strata=strata, n_boot=300, seed=7)
    assert c1 == two_way_bootstrap_ci(res, strata=strata, n_boot=300, seed=7)
    assert c1.low <= c1.point <= c1.high
    assert c1.point == sum(r.passed for r in res) / len(res)


def test_two_way_bootstrap_coverage_sim():
    rng = random.Random(2026)
    p, hits = 0.85, 0
    for i in range(200):
        res = grid(p, 0.3, 20, 15, rng)
        strata = {f"s{s}": "x" for s in range(20)}
        ci = two_way_bootstrap_ci(res, strata=strata, n_boot=150, seed=i)
        hits += ci.low <= p <= ci.high
    assert hits / 200 >= 0.90


def test_corrected_pass_ci_contains_point():
    res = grid(0.9, 0.3, 20, 10, random.Random(3))
    strata = {f"s{s}": "x" for s in range(20)}
    labels = [Label(True, True)] * 22 + [Label(True, False)] * 2 + [Label(False, False)] * 5 \
        + [Label(False, True)]  # fmt: skip
    ci = corrected_pass_ci(res, labels, strata=strata, n_boot=400, seed=5)
    assert ci.low <= ci.point <= ci.high
    assert ci == corrected_pass_ci(res, labels, strata=strata, n_boot=400, seed=5)


def test_p_target_monotonic_in_n():
    kw = dict(icc=0.2, p0=0.92, n_sim=60, seed=1)
    small = p_target(n_students=8, n_templates=6, **kw)
    big = p_target(n_students=40, n_templates=12, **kw)
    assert 0 <= small <= big <= 1 and big > small
