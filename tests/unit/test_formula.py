import math
import random

import pytest

from evals.latency.formula import (
    ClassQuantiles,
    LogNormal,
    fit_lognormal,
    graph_budget_s,
    simulate_class,
    targets,
)


def comps(sigma=0.0):
    return {
        "tool_call": LogNormal(math.log(2.0), sigma),
        "answer_call": LogNormal(math.log(3.0), sigma),
        "overhead": LogNormal(math.log(0.3), sigma),
    }


def test_fit_lognormal_recovers_params():
    rng = random.Random(1)
    f = fit_lognormal([rng.lognormvariate(1.2, 0.3) for _ in range(2000)])
    assert f.mu == pytest.approx(1.2, rel=0.05) and f.sigma == pytest.approx(0.3, rel=0.05)


def test_simulate_zero_variance_is_sum():
    q = simulate_class(comps(), [2], 0.0, n=1000, seed=1)
    assert q.p95 == pytest.approx(2 * 2.0 + 3.0 + 0.3)
    assert q.p50 == pytest.approx(q.p99)


def test_ttfe_card_equals_total():
    q = simulate_class(comps(0.3), [0], 0.0, n=5000, seed=1)
    assert q.ttfe_p95 == q.p95
    q2 = simulate_class(comps(0.3), [1], 0.0, n=5000, seed=1)
    assert q2.ttfe_p95 < q2.p95  # TTFE = 1º progress, após a 1ª chamada que emite ferramenta


def test_guard_retry_inside_draw():
    base = simulate_class(comps(), [1], 0.0, n=1000, seed=1)
    retry = simulate_class(comps(), [1], 1.0, n=1000, seed=1)
    assert retry.p50 == pytest.approx(base.p50 + 3.0)


def test_target_is_max_initial_predicted():
    pred = {"card": ClassQuantiles(3.0, 5.0, 7.0, 9.0, 7.0), "1": ClassQuantiles(1, 2, 3, 4, 1)}
    t = targets({"card": {"p50": 3.5, "p95": 6.0}, "1": {"p95": 8.0}}, pred)
    assert t["card"] == {"p50": 3.5, "p90": 5.0, "p95": 7.0, "p99": 9.0, "ttfe_p95": 7.0}
    assert t["1"]["p95"] == 8.0 and t["1"]["p99"] == 4


def test_budget_is_p99_2plus_times_1_2():
    assert graph_budget_s({"2+": ClassQuantiles(1, 2, 3, 10.0, 1)}) == pytest.approx(12.0)


def test_card_ttfe_follows_final_p95_target():
    pred = {"card": ClassQuantiles(3.0, 4.7, 5.5, 7.25, 5.5)}
    t = targets({"card": {"p95": 6.0}}, pred)
    assert t["card"]["p95"] == 6.0 and t["card"]["ttfe_p95"] == 6.0


@pytest.mark.parametrize("bad", [[], [1.0], [1.0, 0.0], [1.0, -2.0]])
def test_fit_lognormal_rejects_bad_samples(bad):
    with pytest.raises(ValueError, match="amostras"):
        fit_lognormal(bad)
