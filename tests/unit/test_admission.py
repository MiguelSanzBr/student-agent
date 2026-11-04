import pytest

from app.config import AppSettings
from app.infra.stores.admission import AdmissionBucket
from tests.unit.redis_fake import FakeRedis

MODEL = "gemini-3.8-flash"


def bucket(app_secrets: object, rpm: int = 100, tpm: int = 10_000) -> AdmissionBucket:
    s = AppSettings()
    s.slo.quotas[MODEL].rpm, s.slo.quotas[MODEL].tpm = rpm, tpm
    return AdmissionBucket(FakeRedis(), s, MODEL)


async def test_reserve_settle_rpm_tpm(app_secrets: object) -> None:
    b = bucket(app_secrets)
    tickets = [await b.reserve(1, 10) for _ in range(90)]
    assert all(t.ok for t in tickets)
    assert not (await b.reserve(1, 10)).ok  # 91ª: 90% de 100
    await b.settle(tickets[0].ticket or "", 0, 0)  # devolve a diferença
    assert (await b.reserve(1, 10)).ok


async def test_tpm_limits_and_settle_charges_extra(app_secrets: object) -> None:
    b = bucket(app_secrets, tpm=1000)  # capacidade 900 tokens
    t = await b.reserve(1, 800)
    assert t.ok and not (await b.reserve(1, 200)).ok
    await b.settle(t.ticket or "", 1, 899)  # real maior que o estimado é cobrado
    assert not (await b.reserve(1, 2)).ok


async def test_retry_after_returned(app_secrets: object) -> None:
    b = bucket(app_secrets, rpm=1)  # 0 de capacidade
    r = await b.reserve(1, 1)
    assert not r.ok and r.ticket is None
    assert r.retry_after_s is not None and 1 <= r.retry_after_s <= 60


@pytest.mark.parametrize("n", [1])
async def test_settle_unknown_ticket_is_noop(app_secrets: object, n: int) -> None:
    await bucket(app_secrets).settle("nao-existe", 1, 1)
