from uuid import UUID

from app.config import Limits
from app.infra.redis import RedisClient
from core.clock import wall_now
from core.rkey import rkey

_HIT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], 120) end
return n
"""


class RateLimit:
    """Mensagens por minuto e por aluno (janela fixa de 60 s; vale para todos os canais)."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._max = redis, limits.msgs_per_min

    async def hit(self, sub: UUID) -> int | None:
        """Segundos de espera ou None."""
        now = wall_now().timestamp()
        n = await self._r.eval(_HIT, [rkey("rate", sub, "-", str(int(now // 60)))], [])
        return None if int(n) <= self._max else max(1, int(60 - now % 60) + 1)
