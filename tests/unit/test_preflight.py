from decimal import Decimal

from evals.preflight import project_round

M = "gemini-3.8-flash"


def test_preflight_blocks_over_60pct() -> None:
    cost = project_round(100, 3, 5000, M, spend_today=Decimal(0), ceiling=Decimal(10**6),
                         rpd_left=10**6).cost_usd  # fmt: skip

    def run(frac: str, rpd_left: int = 10**6) -> bool:
        ceiling = cost / Decimal(frac)
        return project_round(100, 3, 5000, M, spend_today=Decimal(0), ceiling=ceiling,
                             rpd_left=rpd_left).ok  # fmt: skip

    assert run("0.59") and not run("0.61")
    assert not run("0.01", rpd_left=int(300 / 0.61))  # 300 chamadas = 61% do RPD
    p = project_round(10, 2, 1000, M, spend_today=Decimal(0), ceiling=Decimal(100), rpd_left=1000)
    assert p.calls == 20 and p.ok


def test_preflight_uses_remaining_not_ceiling() -> None:
    p = project_round(100, 3, 5000, M, spend_today=Decimal(19), ceiling=Decimal(20), rpd_left=10**6)
    assert not p.ok
