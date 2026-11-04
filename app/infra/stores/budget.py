from uuid import UUID

from app.config import Limits
from app.infra.redis import RedisClient
from core.clock import wall_now
from core.rkey import RKey, rkey

_ADD = """
local n = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], 172800)
return n
"""


class TokenBudget:
    """Tokens por dia (UTC) e por aluno; o acerto é depois do turno, com o uso real."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._max = redis, limits.tokens_per_day

    @staticmethod
    def _key(sub: UUID) -> RKey:
        return rkey("budget", sub, "-", wall_now().strftime("%Y%m%d"))

    async def used(self, sub: UUID) -> int:
        raw = await self._r.get(self._key(sub))
        return int(raw) if raw else 0

    async def check(self, sub: UUID) -> int | None:
        """Segundos até o próximo dia UTC se a cota acabou, senão None."""
        if await self.used(sub) < self._max:
            return None
        now = wall_now()
        return 86400 - (now.hour * 3600 + now.minute * 60 + now.second)

    async def add(self, sub: UUID, tokens: int) -> None:
        await self._r.eval(_ADD, [self._key(sub)], [tokens])
