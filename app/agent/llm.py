from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.tools.base import ToolSpec


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: Mapping[str, Any]
    provider_meta: bytes | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cached_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str | None
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    model: str
    finish_reason: str = "STOP"
    provider_meta: bytes | None = None


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool", "system_note"]
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: Mapping[str, Any] | None = None
    untrusted: bool = False
    provider_meta: bytes | None = None
    model: str | None = None
    turn: int = 0


# "none": ferramentas declaradas (prefixo e cache intactos), mas o modelo não pode chamar.
ToolChoice = Literal["auto", "none"]


class LLMProvider(Protocol):
    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse: ...  # fmt: skip


class LLMUnavailable(Exception):
    def __init__(self, reason: Literal["timeout", "quota", "server"]) -> None:
        super().__init__(reason)
        self.reason = reason
