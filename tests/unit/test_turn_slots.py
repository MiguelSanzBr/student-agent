import asyncio

from app.config import AppSettings
from app.infra.stores.admission import TurnSlots
from tests.unit.redis_fake import FakeRedis


def slots(cap: int) -> TurnSlots:
    s = AppSettings()
    s.slo.turn_slots = cap
    return TurnSlots(FakeRedis(), s)


async def test_slot_lease_expires_without_release(app_secrets: object) -> None:
    sl = slots(1)
    assert await sl.acquire("a", 0.1)
    assert not await sl.acquire("b", 5)
    await asyncio.sleep(0.2)
    assert await sl.acquire("b", 5)


async def test_slot_release_frees_capacity(app_secrets: object) -> None:
    sl = slots(1)
    assert await sl.acquire("a", 60)
    assert not await sl.acquire("b", 60)
    await sl.release("a")
    assert await sl.acquire("b", 60)
