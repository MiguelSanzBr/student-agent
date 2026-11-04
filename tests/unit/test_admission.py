import asyncio

import pytest

from app.config import AppSettings
from app.infra.stores.admission import AdmissionBucket
from tests.unit.redis_fake import FakeRedis

MODEL = "gemini-3.8-flash"


class Clock:
    def __init__(self, t: float = 600.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def bucket(
    rpm: int = 100, tpm: int = 1_000_000, clock: Clock | None = None, model: str = MODEL
) -> AdmissionBucket:
    s = AppSettings()
    s.slo.quotas[model].rpm, s.slo.quotas[model].tpm = rpm, tpm
    return AdmissionBucket(FakeRedis(), s, model, clock=clock or Clock())


async def fill(b: AdmissionBucket, n: int, tokens: int = 1) -> list[str]:
    out = []
    for _ in range(n):
        r = await b.reserve(1, tokens)
        assert r.ok and r.ticket
        out.append(r.ticket)
    return out


async def test_reserve_settle_rpm_tpm(app_secrets: object) -> None:
    b = bucket()
    tickets = await fill(b, 90)  # exatamente a capacidade: 90% de 100
    assert not (await b.reserve(1, 1)).ok  # 91ª
    await b.settle(tickets[0], 0, 0)  # devolve a diferença
    assert (await b.reserve(1, 1)).ok


async def test_tpm_limits_and_settle_charges_extra(app_secrets: object) -> None:
    b = bucket(tpm=1000)  # capacidade 900 tokens
    t = await b.reserve(1, 800)
    assert t.ok and not (await b.reserve(1, 200)).ok
    await b.settle(t.ticket or "", 1, 899)  # real maior que o estimado é cobrado
    assert not (await b.reserve(1, 2)).ok


async def test_retry_after_returned(app_secrets: object) -> None:
    r = await bucket(rpm=1).reserve(1, 1)  # capacidade 0
    assert not r.ok and r.ticket is None
    assert r.retry_after_s is not None and 1 <= r.retry_after_s <= 60


async def test_settle_unknown_ticket_leaves_state_unchanged(app_secrets: object) -> None:
    b = bucket(rpm=10)  # capacidade 9
    await b.settle("nao-existe", 0, 0)
    await fill(b, 9)
    assert not (await b.reserve(1, 1)).ok


async def test_double_settle_counts_once(app_secrets: object) -> None:
    b = bucket(rpm=10)
    ts = await fill(b, 9)
    await b.settle(ts[0], 0, 0)
    await b.settle(ts[0], 0, 0)
    await fill(b, 1)
    assert not (await b.reserve(1, 1)).ok  # só uma vaga foi devolvida


async def test_concurrent_reserves_admit_exactly_cap(app_secrets: object) -> None:
    b = bucket(rpm=100)
    rs = await asyncio.gather(*(b.reserve(1, 1) for _ in range(150)))
    assert sum(r.ok for r in rs) == 90


async def test_boundary_burst_does_not_exceed_cap_over_rolling_minute(
    app_secrets: object,
) -> None:
    clk = Clock(659.0)  # 1 s antes da virada da janela
    b = bucket(rpm=100, clock=clk)
    await fill(b, 90)
    clk.t = 660.5  # janela nova: janela fixa admitiria mais 90 aqui
    assert not (await b.reserve(1, 1)).ok
    clk.t = 690.0  # metade do peso da janela anterior sobrou: ~45 de vaga
    admitted = sum([(await b.reserve(1, 1)).ok for _ in range(90)])
    assert 40 <= admitted <= 46


async def test_rollover_after_idle_restores_full_capacity(app_secrets: object) -> None:
    clk = Clock(659.0)
    b = bucket(rpm=100, clock=clk)
    await fill(b, 90)
    clk.t = 659.0 + 120
    await fill(b, 90)
    assert not (await b.reserve(1, 1)).ok


async def test_settle_after_window_flip_adjusts_previous_window(app_secrets: object) -> None:
    clk = Clock(659.0)
    b = bucket(rpm=100, clock=clk)
    ts = await fill(b, 90)
    clk.t = 661.0
    for t in ts:
        await b.settle(t, 0, 0)  # devolve à janela anterior
    assert (await b.reserve(1, 1)).ok


async def test_stale_settle_after_two_windows_is_noop(app_secrets: object) -> None:
    clk = Clock(610.0)
    b = bucket(rpm=10, clock=clk)
    ts = await fill(b, 9)
    clk.t = 800.0
    await b.settle(ts[0], 0, 0)
    await fill(b, 9)  # nenhum crédito indevido
    assert not (await b.reserve(1, 1)).ok


@pytest.mark.parametrize("model", ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
async def test_bucket_works_for_each_model(app_secrets: object, model: str) -> None:
    s = AppSettings(llm_model=model)
    b = AdmissionBucket(FakeRedis(), s, s.llm_model)
    assert (await b.reserve(1, 1)).ok


async def test_utilization_reads_without_reserving(app_secrets: object) -> None:
    await FakeRedis()._r.flushall()
    b = bucket()
    assert await b.utilization() == 0
    await fill(b, 45)  # metade dos 90
    assert await b.utilization() == pytest.approx(0.5, abs=0.01)
    assert await b.utilization() == pytest.approx(0.5, abs=0.01)  # ler não consome
    await fill(b, 45)
    assert await b.utilization() == pytest.approx(1.0, abs=0.01)
    await FakeRedis()._r.flushall()
