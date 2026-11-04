"""Preflight de custo: todo runner de avaliação chama `project_round` antes de começar."""

from dataclasses import dataclass
from decimal import Decimal

from app.agent.llm import Usage
from app.obs.cost import cost_usd
from core.clock import wall_now

SAFETY = Decimal("0.6")


@dataclass(frozen=True)
class Projection:
    cost_usd: Decimal
    calls: int
    ok: bool


def project_round(
    items: int, calls_per_item: float, tokens_per_call: int, model: str,
    *, spend_today: Decimal, ceiling: Decimal, rpd_left: int,
) -> Projection:  # fmt: skip
    """ok só se custo ≤ 0,6 × (teto − gasto) e chamadas ≤ 0,6 × RPD restante. Conservador: todos
    os tokens são cobrados como saída."""
    calls = round(items * calls_per_item)
    cost = calls * cost_usd(Usage(output_tokens=tokens_per_call), model, wall_now().date())
    return Projection(
        cost, calls, cost <= SAFETY * (ceiling - spend_today) and calls <= SAFETY * rpd_left
    )
