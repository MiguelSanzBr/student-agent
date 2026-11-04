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


def _samples(rng, mu=1.5, n=300):
    return [rng.lognormvariate(mu, 0.3) for _ in range(n)]


def test_refit_after_window_a_updates_targets():
    from app.config import load_slo_targets
    from evals.latency.formula import refit_after_window_a

    cur = load_slo_targets()
    new = refit_after_window_a({"tool_call": _samples(random.Random(3))}, cur, n=20_000)
    assert new.components["tool_call"].mu == pytest.approx(1.5, abs=0.1)
    assert new.components["answer_call"] == cur.components["answer_call"]  # sem amostra: mantém
    assert new.targets["2+"].p95 > cur.targets["2+"].p95
    assert new.graph_budget_s == pytest.approx(new.targets["2+"].p99 * 1.2, abs=0.02)
    assert new.frozen is False and new.quotas == cur.quotas and cur.components != new.components


def test_refit_refuses_frozen_and_small_samples():
    from app.config import load_slo_targets
    from evals.latency.formula import refit_after_window_a

    cur = load_slo_targets()
    rng = random.Random(1)
    with pytest.raises(ValueError, match="frozen"):
        refit_after_window_a({"tool_call": _samples(rng)}, cur.model_copy(update={"frozen": True}))
    with pytest.raises(ValueError, match="insuficientes"):
        refit_after_window_a({"tool_call": _samples(rng, n=29)}, cur)
    with pytest.raises(ValueError, match="desconhecido"):
        refit_after_window_a({"nope": _samples(rng)}, cur)


def test_refit_is_pure_and_matches_current_targets_with_current_components():
    """Sem amostras novas, refit com as premissas do yaml reproduz as metas do yaml (±0,05 s)."""
    from app.config import load_slo_targets
    from evals.latency.formula import refit_after_window_a

    cur = load_slo_targets()
    new = refit_after_window_a({}, cur)
    for cls, q in cur.targets.items():
        for k in ("p50", "p90", "p95", "p99"):
            assert getattr(new.targets[cls], k) == pytest.approx(getattr(q, k), abs=0.05), (cls, k)
    assert cur == load_slo_targets()  # `current` não foi mutado
