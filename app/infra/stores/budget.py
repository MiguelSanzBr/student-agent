from uuid import UUID


class TokenBudget:  # T25
    async def check(self, sub: UUID) -> int | None:
        raise NotImplementedError

    async def add(self, sub: UUID, tokens: int) -> None:
        raise NotImplementedError
