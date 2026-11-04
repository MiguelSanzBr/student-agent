import secrets
from uuid import UUID

from app.config import Limits
from app.infra.redis import RedisClient
from core.rkey import rkey

_TAKE = """
local v = redis.call('GET', KEYS[1])
if v then redis.call('DEL', KEYS[1]) end
return v
"""


class LoginTickets:
    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._ttl = redis, limits.login_ticket_ttl_s

    async def issue(self, person_key: UUID) -> str:
        tid = secrets.token_urlsafe(32)
        await self._r.set(
            rkey("ticket", "global", "-", tid), str(person_key).encode(), ex=self._ttl
        )
        return tid

    async def consume(self, ticket: str) -> UUID | None:
        """Uso único: GET+DEL atômico."""
        raw = await self._r.eval(_TAKE, [rkey("ticket", "global", "-", ticket)], [])
        return UUID(raw.decode()) if raw else None
