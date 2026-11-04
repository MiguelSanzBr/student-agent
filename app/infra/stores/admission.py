from dataclasses import dataclass


@dataclass(frozen=True)
class Reservation:
    ok: bool
    retry_after_s: int | None
    ticket: str | None


class AdmissionBucket:  # T4
    async def reserve(self, est_requests: int, est_tokens: int) -> Reservation:
        raise NotImplementedError

    async def settle(self, ticket: str, real_requests: int, real_tokens: int) -> None:
        raise NotImplementedError


class TurnSlots:  # T4
    async def acquire(self, ticket: str, ttl_s: float) -> bool:
        raise NotImplementedError

    async def release(self, ticket: str) -> None:
        raise NotImplementedError
