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


async def test_one_student_cannot_take_every_slot(app_secrets: object) -> None:
    from uuid import uuid4

    sl = slots(8)
    a, b = uuid4(), uuid4()
    assert [await sl.acquire_for(f"a{i}", 60, a) for i in range(3)] == [
        1,
        1,
        -1,
    ]  # teto do aluno: 2
    assert await sl.acquire_for("b0", 60, b) == 1  # as vagas globais seguem livres para os outros
    await sl.release("a0", a)
    assert await sl.acquire_for("a3", 60, a) == 1


async def test_global_slots_full_still_reported(app_secrets: object) -> None:
    from uuid import uuid4

    sl = slots(1)
    assert await sl.acquire_for("x", 60, uuid4()) == 1
    assert await sl.acquire_for("y", 60, uuid4()) == 0
