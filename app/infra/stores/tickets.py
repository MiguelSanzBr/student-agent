import secrets
from uuid import UUID

from app.config import Limits
from app.infra.redis import GETDEL, RedisClient
from core.rkey import rkey


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
        raw = await self._r.eval(GETDEL, [rkey("ticket", "global", "-", ticket)], [])
        return UUID(raw.decode()) if raw else None
