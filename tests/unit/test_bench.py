import random

from evals.latency.bench import Sample, summarize


def _samples(n: int, cls: str, total: float, ttfe: float) -> list[Sample]:
    rng = random.Random(1)
    return [
        Sample(f"s{i % 10}", cls, total * rng.uniform(0.9, 1.1), ttfe, {"answer_call": 2.0},
               status="ok", cost_usd=0.002, cached=100, input_tokens=1000)
        for i in range(n)
    ]  # fmt: skip


def test_summarize_checks_every_quantile_against_targets() -> None:
    xs = _samples(100, "card", 4.0, 4.0) + _samples(100, "2+", 30.0, 3.0)
    rep = summarize(xs, seed=1, n_boot=200)
    assert rep["classes"]["card"]["pass"] is True
    assert rep["classes"]["2+"]["pass"] is False  # 30 s > p50..p99 de 2+
    assert set(rep["classes"]["card"]["quantiles"]) == {"p50", "p90", "p95", "p99"}
    q = rep["classes"]["card"]["quantiles"]["p95"]
    assert q["ci"][0] <= q["value"] <= q["ci"][1] and q["target"] == 7.9
    assert rep["classes"]["card"]["ttfe_p95"]["target"] == 7.9
    assert rep["share"] == {"card": 0.5, "2+": 0.5}
    assert rep["cost_per_turn_usd"] == 0.002 and rep["budget_exceeded_rate"] == 0.0
    assert rep["cache_ratio"] == 0.1
    assert "answer_call" in rep["classes"]["card"]["cascade"]
