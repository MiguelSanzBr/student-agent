from decimal import Decimal
from typing import Literal

from app.config import Limits
from app.infra.redis import RedisClient
from core.clock import wall_now
from core.rkey import RKey, rkey

_MICRO = 1_000_000
_ADD = """
local n = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], 172800)
return n
"""


class GlobalSpend:
    """Gasto global do dia (UTC) em micro-US$ contra o teto do perfil."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r = redis
        self._ceiling = limits.global_ceiling_usd * _MICRO
        self._degrade = None if limits.degrade_at is None else limits.degrade_at * self._ceiling

    @staticmethod
    def _key() -> RKey:
        return rkey("spend", "global", "-", wall_now().strftime("%Y%m%d"))

    async def level(self) -> Literal["normal", "degraded", "blocked"]:
        raw = await self._r.get(self._key())
        spent = int(raw) if raw else 0
        if spent >= self._ceiling:
            return "blocked"
        return "degraded" if self._degrade is not None and spent >= self._degrade else "normal"

    async def add(self, usd: Decimal) -> None:
        await self._r.eval(_ADD, [self._key()], [int(usd * _MICRO)])
