from uuid import UUID


class RateLimit:  # T25
    async def hit(self, sub: UUID) -> int | None:
        """Segundos de espera ou None."""
        raise NotImplementedError
