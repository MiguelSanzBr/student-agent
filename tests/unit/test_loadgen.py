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


def _step(rate: float, *, cut: int = 0, p95: float = 10.0, bad503: int = 0,
          reasons: dict[str, int] | None = None) -> StepResult:  # fmt: skip
    return StepResult(
        rate=rate, sent=1000, ok=900, rejected=100 - bad503, rejected_no_retry_after=bad503,
        retries=50, cut=cut, latencies=[p95] * 900,
        reasons={"quota": 100 - bad503} if reasons is None else reasons,
    )  # fmt: skip


def test_ladder_a_criteria() -> None:
    base = 10.0
    good = LadderResult([_step(1), _step(2), _step(4)])
    assert ladder_a_pass(good, base)
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, cut=6)]), base)  # 0,6%
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, p95=11.2)]), base)  # +12%
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, bad503=1)]), base)


def test_ladder_edge_cases_and_yaml_constants() -> None:
    import inspect

    from app.config import load_slo_targets

    assert not ladder_a_pass(LadderResult([]), 10.0)
    assert not ladder_a_pass(LadderResult([_step(1), StepResult(2, 0, 0, 0, 0, 0, 0)]), 10.0)
    p = inspect.signature(ladder_a_pass).parameters
    yaml = load_slo_targets().model_constants["ladder_a"]
    assert {k: p[k].default for k in yaml} == yaml


def test_ladder_a_requires_quota_dominated_rejections_at_top() -> None:
    base = 10.0
    slots = {"slots": 80, "quota": 20}
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, reasons=slots)]), base)
    assert not ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, reasons={})]), base)
    ok = {"quota": 60, "slots": 40}
    assert ladder_a_pass(LadderResult([_step(1), _step(2), _step(4, reasons=ok)]), base)
    no_rej = StepResult(4, 1000, 1000, 0, 0, 50, 0, [10.0] * 1000)
    assert not ladder_a_pass(
        LadderResult([_step(1), no_rej]), base
    )  # 2x sem rejeição: nada testado
