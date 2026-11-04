import json
import re
from uuid import UUID

from app.config import Limits
from app.infra.redis import GETDEL, RedisClient
from app.obs.metrics import METRICS
from app.tools.base import DraftCard, DraftKey
from core.rkey import RKey, rkey

EXPIRY_MARKER_TTL_S = 1800  # sobrevive ao rascunho (600 s) para que "expirado" seja observável
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # formato dos draft_id; qualquer outra coisa é inválida


def _enc(c: DraftCard) -> bytes:
    d = {**c.__dict__, "key": [c.key.service_code, c.key.subject_code]}
    return json.dumps(d).encode()


def _dec(raw: bytes) -> DraftCard:
    d = json.loads(raw)
    return DraftCard(**{**d, "key": DraftKey(*d["key"])})


def _try_dec(raw: bytes) -> DraftCard | None:
    try:
        return _dec(raw)
    except (ValueError, TypeError, KeyError):
        return None


class DraftStore:
    """Rascunhos só no canal web (canal fixo "web" nas chaves)."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._ttl = redis, limits.draft_ttl_s

    @staticmethod
    def _k(sub: UUID, prefix: str, id: str) -> RKey:
        # prefixos distintos: rascunho (d), ponteiro da conversa (p), marcador de expiração (x)
        return rkey("draft", sub, "web", f"{prefix}:{id}")

    async def put(self, sub: UUID, conversation_id: str, card: DraftCard) -> None:
        await self._r.set(self._k(sub, "d", card.draft_id), _enc(card), ex=self._ttl)
        await self._r.set(self._k(sub, "p", conversation_id), card.draft_id.encode(), ex=self._ttl)
        await self._r.set(self._k(sub, "x", conversation_id), b"1", ex=EXPIRY_MARKER_TTL_S)

    async def take(self, sub: UUID, draft_id: str) -> DraftCard | None:
        """Uso único e preso ao dono (o `sub` entra na chave)."""
        if not _ID.fullmatch(draft_id):
            return None
        raw = await self._r.eval(GETDEL, [self._k(sub, "d", draft_id)], [])
        card = _try_dec(raw) if raw is not None else None
        if card is None:
            return None
        await self._r.delete(self._k(sub, "p", card.conversation_id))
        await self._r.delete(self._k(sub, "x", card.conversation_id))
        return card

    async def pending(self, sub: UUID, conversation_id: str) -> DraftCard | None:
        ptr = await self._r.get(self._k(sub, "p", conversation_id))
        raw = await self._r.get(self._k(sub, "d", ptr.decode())) if ptr else None
        if (card := _try_dec(raw) if raw is not None else None) is not None:
            return card
        marker = self._k(sub, "x", conversation_id)
        if await self._r.get(marker) is not None:  # o rascunho venceu sem ser usado
            await self._r.delete(marker)
            METRICS["agent_draft_total"].labels(outcome="expirado").inc()  # type: ignore[union-attr]
        return None

    async def same_pending(
        self, sub: UUID, conversation_id: str, key: DraftKey
    ) -> DraftCard | None:
        card = await self.pending(sub, conversation_id)
        return card if card is not None and card.key == key else None
