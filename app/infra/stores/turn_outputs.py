import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from app.infra.redis import RedisClient
from core.rkey import RKey, rkey

TTL_S = 1800


class TurnOutputCache:
    """Saídas tipadas do turno para o /feedback; canal fixo "-" (o feedback só tem o trace_id)."""

    def __init__(self, redis: RedisClient) -> None:
        self._r = redis

    @staticmethod
    def _k(sub: UUID, trace_id: str) -> RKey:
        return rkey("turn_out", sub, "-", trace_id)

    async def put(self, sub: UUID, trace_id: str, payload: Mapping[str, Any]) -> None:
        await self._r.set(self._k(sub, trace_id), json.dumps(payload).encode(), ex=TTL_S)

    async def get(self, sub: UUID, trace_id: str) -> Mapping[str, Any] | None:
        raw = await self._r.get(self._k(sub, trace_id))
        return json.loads(raw) if raw else None
