import asyncio
import math
import random
from pathlib import Path
from typing import Any

import pytest

from app.agent import make_llm
from app.agent.levers import HedgeBudget, Hedger, first_call_delay_s
from app.agent.llm import LLMResponse, Usage
from app.config import AppSettings, load_slo_targets
from tests.unit.turn_env import OK, SCOPE, SUB, make_service, patch_db, scripted

SLO = load_slo_targets()
H = SLO.hedge


def test_levers_off_by_default(app_secrets: Path) -> None:
    s = AppSettings()
    assert s.lever_hedge_first_call is False
    assert make_service(s, make_llm(AppSettings(llm_provider="fake"))).hedger is None
    assert make_service(AppSettings(lever_hedge_first_call=True), scripted(OK)).hedger is not None


def test_sentence_stream_lever_removed(app_secrets: Path) -> None:
    assert not hasattr(AppSettings(), "lever_sentence_stream")


def test_hedge_delay_is_first_call_mixture_p90_not_tool_p90() -> None:
    c = SLO.components
    delay = first_call_delay_s(c, H)
    tool_p90 = math.exp(c["tool_call"].mu + 1.2816 * c["tool_call"].sigma)
    assert delay > tool_p90 * 1.1
    rng = random.Random(5)
    n = 20_000
    card = [rng.lognormvariate(c["answer_call"].mu, c["answer_call"].sigma) for _ in range(n)]
    tool = [rng.lognormvariate(c["tool_call"].mu, c["tool_call"].sigma) for _ in range(n)]
    card_fire = sum(x > delay for x in card) / n
    old_card_fire = sum(x > tool_p90 for x in card) / n
    mix_fire = H.card_share * card_fire + (1 - H.card_share) * sum(x > delay for x in tool) / n
    assert old_card_fire > 0.4  # o defeito: p90 de tool_call dispara em ~45% dos card
    assert card_fire < 0.2 and mix_fire == pytest.approx(1 - H.quantile, abs=0.01)


def test_hedge_budget_fraction_and_floor() -> None:
    t = [0.0]
    b = HedgeBudget(H, clock=lambda: t[0])
    assert [b.allow() for _ in range(4)] == [True, True, True, False]  # piso de 3/min
    t[0] = 61.0  # a janela esvaziou
    for _ in range(100):
        b.note_call()
    assert sum(b.allow() for _ in range(30)) == 10  # 10% de 100


class _Slow:
    """1ª requisição trava; a 2ª responde logo. Grava quem foi cancelado."""

    def __init__(self) -> None:
        self.n = 0
        self.cancelled: list[int] = []

    async def generate(self, **_: Any) -> LLMResponse:
        self.n += 1
        me = self.n
        try:
            await asyncio.sleep(10 if me == 1 else 0)
        except asyncio.CancelledError:
            self.cancelled.append(me)
            raise
        return LLMResponse(f"r{me}", (), Usage(100, 20), "m")


async def _free() -> bool:
    return False


async def _full() -> bool:
    return True


def _hedger(delay: float = 0.01, sat: Any = _free, budget: HedgeBudget | None = None) -> Hedger:
    return Hedger(delay, budget or HedgeBudget(H), sat)


async def test_hedge_cancels_loser() -> None:
    inner = _Slow()
    r, hedged = await _hedger().run(lambda: inner.generate())
    assert r.text == "r2" and hedged and inner.cancelled == [1]


async def test_hedge_not_fired_when_first_is_fast() -> None:
    n = 0

    async def fast() -> LLMResponse:
        nonlocal n
        n += 1
        return LLMResponse("ok", (), Usage(1, 1), "m")

    r, hedged = await _hedger(0.05).run(fast)
    assert n == 1 and not hedged


async def test_hedge_skipped_when_budget_spent_or_bucket_saturated() -> None:
    class Quick(_Slow):
        async def generate(self, **_: Any) -> LLMResponse:
            self.n += 1
            await asyncio.sleep(0.03)
            return LLMResponse("only", (), Usage(1, 1), "m")

    for h in (
        _hedger(sat=_full),
        _hedger(budget=HedgeBudget(H.model_copy(update={"floor_per_min": 0, "max_fraction": 0}))),
    ):
        inner = Quick()
        r, hedged = await h.run(inner.generate)
        assert inner.n == 1 and not hedged and r.text == "only"


@pytest.fixture
def _db(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_db(monkeypatch)


@pytest.mark.usefixtures("app_secrets", "_db")
async def test_hedged_turn_charges_two_calls_through_settle() -> None:
    svc = make_service(AppSettings(lever_hedge_first_call=True), _Slow())
    assert svc.hedger is not None
    await svc.redis._r.flushall()  # type: ignore[attr-defined]  # fakeredis é compartilhado
    svc.hedger.delay_s = 0.01
    seen: list[tuple[int, int]] = []
    real = svc.bucket.settle

    async def spy(ticket: str, calls: int, tokens: int) -> None:
        seen.append((calls, tokens))
        await real(ticket, calls, tokens)

    svc.bucket.settle = spy  # type: ignore[method-assign]
    from tests.unit.test_turn_service import turn

    await turn(svc)
    assert seen == [(2, 2 * 120)]  # 1 chamada real + a 2ª requisição do hedge (estimada)
    assert await svc.budget.used(SUB) == 2 * 120
    assert svc.hedger.budget._hedges  # type: ignore[attr-defined]
    assert SCOPE
    await svc.redis._r.flushall()  # type: ignore[attr-defined]
