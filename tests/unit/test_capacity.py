from decimal import Decimal

from app.config import Quota
from evals.latency.capacity import capacity, monthly_cost


def test_capacity_known_values() -> None:
    c = capacity(2100, 60, 4700, 6, Quota(rpm=1000, tpm=1_000_000, rpd=10_000))
    assert c.turns_per_s == 35
    assert c.in_flight == 210
    assert abs(c.tpm - 10_000_000) / 10_000_000 < 0.02
    assert c.rpm == 2100
    assert c.replicas == 10  # ceil(9,87M / 1M TPM)


def test_monthly_cost_scales_and_doubles_in_2027() -> None:
    a = monthly_cost(100, 10, "gemini-3.8-flash", 2026)
    assert a > 0 and monthly_cost(200, 10, "gemini-3.8-flash", 2026) == 2 * a
    assert monthly_cost(100, 10, "gemini-3.8-flash", 2027) == 2 * a
    assert isinstance(a, Decimal)
