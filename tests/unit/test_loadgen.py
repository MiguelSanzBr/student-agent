import random

from evals.latency.loadgen import LadderResult, Outcome, StepResult, ladder_a_pass, run_ladder


def _ok(_t: float, _r: random.Random) -> Outcome:
    return Outcome(200, 1.0)


def test_loadgen_poisson_rate() -> None:
    res = run_ladder([5, 20, 50], 400, seed=1, handler=_ok)
    for step, rate in zip(res.steps, [5, 20, 50], strict=True):
        assert abs(step.sent / 400 - rate) / rate < 0.05


def test_ladder_is_seeded() -> None:
    a = run_ladder([10], 50, seed=3, handler=_ok)
    assert a == run_ladder([10], 50, seed=3, handler=_ok)


def _step(rate: float, *, cut: int = 0, p95: float = 10.0, bad503: int = 0) -> StepResult:
    return StepResult(
        rate=rate, sent=1000, ok=900, rejected=100 - bad503, rejected_no_retry_after=bad503,
        retries=50, cut=cut, latencies=[p95] * 900,
    )  # fmt: skip


def test_ladder_a_criteria() -> None:
    base = 10.0
    good = LadderResult([_step(1), _step(2), _step(4)])
    assert ladder_a_pass(good, base)
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, cut=6)]), base)  # 0,6%
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, p95=11.2)]), base)  # +12%
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, bad503=1)]), base)
