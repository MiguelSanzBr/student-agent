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


class HistoryStore:
    """Um blob JSON por conversa. shortcut: read-modify-write sem lock; o lease de vaga por turno
    já serializa as escritas de uma conversa, trocar por script Lua se isso mudar."""

    def __init__(self, redis: RedisClient, limits: Limits) -> None:
        self._r, self._lim = redis, limits

    def _key(self, sub: UUID, channel: Channel, conversation_id: str) -> RKey:
        return rkey("conv", sub, channel, conversation_id)

    async def _get(self, key: RKey) -> dict[str, Any]:
        raw = await self._r.get(key)
        return (
            json.loads(raw)
            if raw
            else {"turns": [], "tainted": False, "offered": False, "draft_only": False}
        )

    async def _put(self, key: RKey, blob: Mapping[str, Any]) -> None:
        await self._r.set(key, json.dumps(blob).encode(), ex=self._lim.history_ttl_s)

    async def load(self, sub: UUID, channel: Channel, conversation_id: str) -> Conversation:
        b = await self._get(self._key(sub, channel, conversation_id))
        return Conversation(
            tuple(tuple(_dec(m) for m in t) for t in b["turns"]),
            b["tainted"], b["offered"], b["draft_only"],
        )  # fmt: skip

    async def append(
        self, sub: UUID, channel: Channel, conversation_id: str,
        turn: tuple[Message, ...], *, draft_only: bool,
    ) -> None:  # fmt: skip
        key = self._key(sub, channel, conversation_id)
        b = await self._get(key)
        b["turns"] = [*b["turns"], [_enc(m) for m in turn]][-self._lim.history_turns :]
        b["draft_only"] = draft_only  # o taint mora fora de `turns`: o corte não o apaga
        await self._put(key, b)

    async def _flag(self, sub: UUID, channel: Channel, conversation_id: str, flag: str) -> None:
        key = self._key(sub, channel, conversation_id)
        b = await self._get(key)
        b[flag] = True
        await self._put(key, b)

    async def mark_tainted(self, sub: UUID, channel: Channel, conversation_id: str) -> None:
        await self._flag(sub, channel, conversation_id, "tainted")

    async def mark_other_enrollment_offered(
        self, sub: UUID, channel: Channel, conversation_id: str
    ) -> None:
        await self._flag(sub, channel, conversation_id, "offered")
