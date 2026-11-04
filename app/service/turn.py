from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from app.agent.graph import TurnResult
from core.scope import StudentScope


@dataclass(frozen=True)
class Admission:
    ok: bool
    status: Literal[200, 429, 503]
    retry_after_s: int | None
    ticket: str | None
    reason: Literal["ok", "rate", "budget", "spend", "quota", "slots"]


class TurnService(Protocol):
    async def admit(self, *, scope: StudentScope, channel: Literal["web", "a2a"]) -> Admission: ...

    async def handle(
        self, *, admission: Admission, scope: StudentScope, conversation_id: str, text: str,
        channel: Literal["web", "a2a"], emit: Callable[[Mapping[str, Any]], Awaitable[None]],
    ) -> TurnResult: ...  # fmt: skip

    async def settle(
        self, admission: Admission, result: TurnResult | None, *, cancelled: bool = False
    ) -> None: ...
