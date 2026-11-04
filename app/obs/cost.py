from datetime import date
from decimal import Decimal

from app.agent.llm import Usage

# US$ por 1M tokens: (entrada, saída com raciocínio, entrada em cache). Tabela datada: vale a
# última linha com `desde <= on`. Cache = 10% da entrada (premissa; confirmar na fatura).
_PRICES: dict[str, list[tuple[date, tuple[Decimal, Decimal, Decimal]]]] = {
    "gemini-3.5-flash-lite": [(date.min, (Decimal("0.30"), Decimal("2.50"), Decimal("0.03")))],
    "gemini-3.8-flash": [
        (date.min, (Decimal("0.75"), Decimal("3.75"), Decimal("0.075"))),
        (
            date(2027, 1, 1),
            (Decimal("1.50"), Decimal("7.50"), Decimal("0.15")),
        ),  # dobra em 2027 (spec §6.5)
    ],
}


def cost_usd(usage: Usage, model: str, on: date) -> Decimal:
    if model not in _PRICES:
        raise ValueError(f"sem preço para o modelo {model!r}")
    p_in, p_out, p_cache = [p for since, p in _PRICES[model] if since <= on][-1]
    fresh = usage.input_tokens - usage.cached_tokens  # input_tokens inclui o cache
    total = (
        fresh * p_in + usage.cached_tokens * p_cache
        + (usage.output_tokens + usage.thinking_tokens) * p_out
    )  # fmt: skip
    return total / Decimal(1_000_000)
