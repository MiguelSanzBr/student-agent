from app.config import AppSettings
from app.infra.stores.admission import TurnSlots
from tests.unit.redis_fake import FakeRedis


class Clock:
    t = 1000.0

    def __call__(self) -> float:
        return self.t


def slots(cap: int, clk: Clock | None = None) -> TurnSlots:
    s = AppSettings()
    s.slo.turn_slots = cap
    return TurnSlots(FakeRedis(), s, clock=clk or Clock())


async def test_slot_lease_expires_without_release(app_secrets: object) -> None:
    clk = Clock()
    sl = slots(1, clk)
    assert await sl.acquire("a", 0.1)
    assert not await sl.acquire("b", 5)
    clk.t += 0.2
    assert await sl.acquire("b", 5)


async def test_slot_release_frees_capacity(app_secrets: object) -> None:
    sl = slots(1)
    assert await sl.acquire("a", 60)
    assert not await sl.acquire("b", 60)
    await sl.release("a")
    assert await sl.acquire("b", 60)


async def test_capacity_above_one_and_double_release(app_secrets: object) -> None:
    sl = slots(3)
    assert all([await sl.acquire(t, 60) for t in "abc"])
    assert not await sl.acquire("d", 60)
    await sl.release("a")
    await sl.release("a")  # idempotente: não libera outra vaga
    assert await sl.acquire("d", 60)
    assert not await sl.acquire("e", 60)
