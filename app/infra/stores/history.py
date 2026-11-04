from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.agent.llm import Message


@dataclass(frozen=True)
class Conversation:
    turns: tuple[tuple[Message, ...], ...]
    tainted: bool
    other_enrollment_offered: bool
    last_turn_draft_only: bool


Channel = Literal["web", "a2a"]


class HistoryStore:  # T14
    async def load(self, sub: UUID, channel: Channel, conversation_id: str) -> Conversation:
        raise NotImplementedError

    async def append(
        self, sub: UUID, channel: Channel, conversation_id: str,
        turn: tuple[Message, ...], *, draft_only: bool,
    ) -> None:  # fmt: skip
        raise NotImplementedError

    async def mark_tainted(self, sub: UUID, channel: Channel, conversation_id: str) -> None:
        raise NotImplementedError

    async def mark_other_enrollment_offered(
        self, sub: UUID, channel: Channel, conversation_id: str
    ) -> None:
        raise NotImplementedError
