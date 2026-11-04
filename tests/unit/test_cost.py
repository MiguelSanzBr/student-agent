from datetime import date
from decimal import Decimal

import pytest

from app.agent.llm import Usage
from app.obs.cost import cost_usd

U = Usage(input_tokens=1_000_000, output_tokens=500_000, thinking_tokens=500_000,
          cached_tokens=200_000)  # fmt: skip


def test_cost_per_model_and_2027_price() -> None:
    d26, d27 = date(2026, 10, 31), date(2027, 1, 1)
    assert cost_usd(U, "gemini-3.5-flash-lite", d26) == Decimal("0.24") + Decimal(
        "0.006"
    ) + Decimal("2.5")
    assert cost_usd(U, "gemini-3.8-flash", d26) == Decimal("0.6") + Decimal("0.015") + Decimal(
        "3.75"
    )
    assert cost_usd(U, "gemini-3.8-flash", d27) == 2 * cost_usd(U, "gemini-3.8-flash", d26)


def test_unknown_model_rejected() -> None:
    with pytest.raises(ValueError):
        cost_usd(U, "x", date(2026, 1, 1))
