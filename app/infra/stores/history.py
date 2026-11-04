import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.agent.llm import Message, ToolCall
from app.config import Limits
from app.infra.redis import RedisClient
from core.rkey import RKey, rkey


@dataclass(frozen=True)
class Conversation:
    turns: tuple[tuple[Message, ...], ...]
    tainted: bool
    other_enrollment_offered: bool
    last_turn_draft_only: bool


Channel = Literal["web", "a2a"]


def _b(v: bytes | None) -> str | None:
    return None if v is None else base64.b64encode(v).decode()


def _unb(v: str | None) -> bytes | None:
    return None if v is None else base64.b64decode(v)


def _enc(m: Message) -> dict[str, Any]:
    return {
        "role": m.role, "text": m.text, "tool_call_id": m.tool_call_id,
        "tool_name": m.tool_name, "tool_result": m.tool_result, "untrusted": m.untrusted,
        "provider_meta": _b(m.provider_meta), "model": m.model, "turn": m.turn,
        "tool_calls": [
            {"id": c.id, "name": c.name, "args": dict(c.args), "provider_meta": _b(c.provider_meta)}
            for c in m.tool_calls
        ],
    }  # fmt: skip


def _dec(d: Mapping[str, Any]) -> Message:
    calls = tuple(
        ToolCall(c["id"], c["name"], c["args"], _unb(c["provider_meta"])) for c in d["tool_calls"]
    )
    return Message(
        d["role"], d["text"], calls, d["tool_call_id"], d["tool_name"], d["tool_result"],
        d["untrusted"], _unb(d["provider_meta"]), d["model"], d["turn"],
    )  # fmt: skip


_APPEND = """
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('LTRIM', KEYS[1], -tonumber(ARGV[3]), -1)
redis.call('EXPIRE', KEYS[1], ARGV[2])
redis.call('SET', KEYS[4], ARGV[4], 'EX', ARGV[2])
redis.call('EXPIRE', KEYS[2], ARGV[2])
redis.call('EXPIRE', KEYS[3], ARGV[2])
return 1
"""
_LOAD = """
return {redis.call('LRANGE', KEYS[1], 0, -1), redis.call('GET', KEYS[2]),
        redis.call('GET', KEYS[3]), redis.call('GET', KEYS[4])}
"""


class HistoryStore:
    """Turnos numa lista (RPUSH+LTRIM atômicos) e flags em chaves próprias: o taint só vai de
    False para True (SET idempotente), então turnos concorrentes da mesma conversa não o perdem."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._lim = redis, limits

    def _keys(self, sub: UUID, channel: Channel, cid: str) -> list[RKey]:
        # o prefixo vem antes do id: ":" no conversation_id nunca colide entre as 4 chaves
        return [rkey("conv", sub, channel, f"{p}:{cid}") for p in ("t", "x", "o", "d")]

    async def load(self, sub: UUID, channel: Channel, conversation_id: str) -> Conversation:
        turns, tainted, offered, draft_only = await self._r.eval(
            _LOAD, self._keys(sub, channel, conversation_id), []
        )
        return Conversation(
            tuple(tuple(_dec(m) for m in json.loads(t)) for t in turns),
            bool(tainted), bool(offered), draft_only == b"1",
        )  # fmt: skip

    async def append(
        self, sub: UUID, channel: Channel, conversation_id: str,
        turn: tuple[Message, ...], *, draft_only: bool,
    ) -> None:  # fmt: skip
        await self._r.eval(
            _APPEND, self._keys(sub, channel, conversation_id),
            [json.dumps([_enc(m) for m in turn]), self._lim.history_ttl_s,
             self._lim.history_turns, "1" if draft_only else "0"],
        )  # fmt: skip

    async def _flag(self, sub: UUID, channel: Channel, conversation_id: str, i: int) -> None:
        key = self._keys(sub, channel, conversation_id)[i]
        await self._r.set(key, b"1", ex=self._lim.history_ttl_s)

    async def mark_tainted(self, sub: UUID, channel: Channel, conversation_id: str) -> None:
        await self._flag(sub, channel, conversation_id, 1)

    async def mark_other_enrollment_offered(
        self, sub: UUID, channel: Channel, conversation_id: str
    ) -> None:
        await self._flag(sub, channel, conversation_id, 2)
