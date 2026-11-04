from uuid import UUID


class LoginTickets:  # T14
    async def issue(self, person_key: UUID) -> str:
        raise NotImplementedError

    async def consume(self, ticket: str) -> UUID | None:
        raise NotImplementedError
