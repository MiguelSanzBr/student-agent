import json
from uuid import UUID

from app.config import Limits
from app.infra.redis import RedisClient
from app.obs.metrics import METRICS
from app.tools.base import DraftCard, DraftKey
from core.rkey import RKey, rkey

EXPIRY_MARKER_TTL_S = 1800  # sobrevive ao rascunho (600 s) para que "expirado" seja observável
_TAKE = """
local v = redis.call('GET', KEYS[1])
if v then redis.call('DEL', KEYS[1]) end
return v
"""


def _enc(c: DraftCard) -> bytes:
    d = {**c.__dict__, "key": [c.key.service_code, c.key.subject_code]}
    return json.dumps(d).encode()


def _dec(raw: bytes) -> DraftCard:
    d = json.loads(raw)
    return DraftCard(**{**d, "key": DraftKey(*d["key"])})


class DraftStore:
    """Rascunhos só no canal web (canal fixo "web" nas chaves)."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._ttl = redis, limits.draft_ttl_s

    @staticmethod
    def _k(sub: UUID, id: str) -> RKey:
        return rkey("draft", sub, "web", id)

    async def put(self, sub: UUID, conversation_id: str, card: DraftCard) -> None:
        await self._r.set(self._k(sub, card.draft_id), _enc(card), ex=self._ttl)
        await self._r.set(
            self._k(sub, f"conv:{conversation_id}"), card.draft_id.encode(), ex=self._ttl
        )
        await self._r.set(self._k(sub, f"exp:{conversation_id}"), b"1", ex=EXPIRY_MARKER_TTL_S)

    async def take(self, sub: UUID, draft_id: str) -> DraftCard | None:
        """Uso único e preso ao dono (o `sub` entra na chave)."""
        raw = await self._r.eval(_TAKE, [self._k(sub, draft_id)], [])
        if raw is None:
            return None
        card = _dec(raw)
        await self._r.delete(self._k(sub, f"conv:{card.conversation_id}"))
        await self._r.delete(self._k(sub, f"exp:{card.conversation_id}"))
        return card

    async def pending(self, sub: UUID, conversation_id: str) -> DraftCard | None:
        ptr = await self._r.get(self._k(sub, f"conv:{conversation_id}"))
        raw = await self._r.get(self._k(sub, ptr.decode())) if ptr else None
        if raw is not None:
            return _dec(raw)
        marker = self._k(sub, f"exp:{conversation_id}")
        if await self._r.get(marker) is not None:  # o rascunho venceu sem ser usado
            await self._r.delete(marker)
            METRICS["agent_draft_total"].labels(outcome="expirado").inc()  # type: ignore[union-attr]
        return None

    async def same_pending(
        self, sub: UUID, conversation_id: str, key: DraftKey
    ) -> DraftCard | None:
        card = await self.pending(sub, conversation_id)
        return card if card is not None and card.key == key else None
