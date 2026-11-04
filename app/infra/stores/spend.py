from decimal import Decimal
from typing import Literal


class GlobalSpend:  # T25
    async def level(self) -> Literal["normal", "degraded", "blocked"]:
        raise NotImplementedError

    async def add(self, usd: Decimal) -> None:
        raise NotImplementedError
