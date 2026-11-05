import math
from decimal import Decimal

from app.config import Quota
from evals.latency.capacity import capacity, monthly_cost


def test_capacity_known_values() -> None:
    c = capacity(2100, 60, 4700, 6, Quota(rpm=1000, tpm=1_000_000, rpd=10_000))
    assert c.turns_per_s == 35
    assert c.in_flight == 210
    assert abs(c.tpm - 10_000_000) / 10_000_000 < 0.02
    assert c.rpm == 4200  # est_requests = 2 chamadas por turno (janela B)
    assert c.replicas == 11  # ceil(9,87M / (0,9 x 1M TPM)); RPM: ceil(4200 / 900) = 5


def test_monthly_cost_scales_and_doubles_in_2027() -> None:
    a = monthly_cost(100, 10, "gemini-3.8-flash", 2026)
    assert a > 0 and monthly_cost(200, 10, "gemini-3.8-flash", 2026) == 2 * a
    assert monthly_cost(100, 10, "gemini-3.8-flash", 2027) == 2 * a
    assert isinstance(a, Decimal)


def test_capacity_defaults_come_from_slo_targets() -> None:
    from app.config import load_slo_targets

    adm = load_slo_targets().admission
    q = Quota(rpm=1000, tpm=10**9, rpd=10_000)
    c = capacity(1000, 60, 1, 1, q)  # só o RPM limita
    assert c.rpm == 1000 / 60 * 60 * adm.est_requests
    assert c.replicas == math.ceil(c.rpm / (1000 * adm.quota_fraction))
    assert capacity(1000, 60, 1, 1, q, calls_per_turn=1, quota_fraction=1.0).replicas == 2


def test_turn_usage_matches_yaml() -> None:
    from app.config import load_slo_targets
    from evals.latency.capacity import TURN_USAGE

    tu = load_slo_targets().model_constants["turn_usage"]
    assert (TURN_USAGE.input_tokens, TURN_USAGE.output_tokens, TURN_USAGE.thinking_tokens) == (
        tu["input"],
        tu["output"],
        tu["thinking"],
    )
