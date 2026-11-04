from uuid import UUID

from app.tools.base import DraftCard, DraftKey


class DraftStore:  # T14
    async def put(self, sub: UUID, conversation_id: str, card: DraftCard) -> None:
        raise NotImplementedError

    async def take(self, sub: UUID, draft_id: str) -> DraftCard | None:
        raise NotImplementedError

    async def pending(self, sub: UUID, conversation_id: str) -> DraftCard | None:
        raise NotImplementedError

    async def same_pending(
        self, sub: UUID, conversation_id: str, key: DraftKey
    ) -> DraftCard | None:
        raise NotImplementedError
