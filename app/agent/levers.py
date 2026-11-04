"""Alavancas de latência, atrás de flag desligada (spec §6.5; medir antes de ligar)."""

import asyncio
import math
from collections import deque
from collections.abc import Awaitable, Callable, Mapping

from app.agent.llm import LLMResponse
from app.config import Hedge, Lognormal
from core.clock import monotonic


def _cdf(x: float, c: Lognormal) -> float:
    return 0.5 * (1 + math.erf((math.log(x) - c.mu) / (c.sigma * math.sqrt(2))))


def first_call_delay_s(components: Mapping[str, Lognormal], cfg: Hedge) -> float:
    """Quantil `cfg.quantile` da 1ª chamada do turno: mistura de `answer_call` (turno `card`, peso
    `card_share`) e `tool_call` (demais). O p90 só de `tool_call` dispararia em ~45% dos `card`."""
    a, t, w = components["answer_call"], components["tool_call"], cfg.card_share
    lo, hi = 1e-3, 1e3
    for _ in range(80):
        mid = math.sqrt(lo * hi)
        if w * _cdf(mid, a) + (1 - w) * _cdf(mid, t) < cfg.quantile:
            lo = mid
        else:
            hi = mid
    return hi


class HedgeBudget:
    """Orçamento de hedges numa janela de 60 s: no máximo `max_fraction` das chamadas, e nunca
    menos que `floor_per_min` (mesma regra do retry do guard)."""

    def __init__(self, cfg: Hedge, clock: Callable[[], float] = monotonic) -> None:
        self._cfg, self._clock = cfg, clock
        self._calls: deque[float] = deque()
        self._hedges: deque[float] = deque()

    def _trim(self) -> None:
        now = self._clock()
        for q in (self._calls, self._hedges):
            while q and now - q[0] >= 60:
                q.popleft()

    def note_call(self) -> None:
        self._calls.append(self._clock())

    def allow(self) -> bool:
        self._trim()
        cap = max(self._cfg.floor_per_min, math.floor(self._cfg.max_fraction * len(self._calls)))
        if len(self._hedges) >= cap:
            return False
        self._hedges.append(self._clock())
        return True


class Hedger:
    """`lever_hedge_first_call`: se a 1ª chamada do turno não responder em `delay_s`, dispara uma
    2ª requisição idêntica (dentro do orçamento e com o balde de admissão folgado). A 1ª que
    terminar bem vence e a outra é cancelada. Quem chama (`_Meter`) contabiliza a 2ª requisição:
    chamada ao balde, tokens e gasto, com a estimativa = uso da vencedora."""

    def __init__(
        self, delay_s: float, budget: HedgeBudget, saturated: Callable[[], Awaitable[bool]]
    ) -> None:
        self.delay_s, self.budget, self._saturated = delay_s, budget, saturated

    async def run(self, call: Callable[[], Awaitable[LLMResponse]]) -> tuple[LLMResponse, bool]:
        self.budget.note_call()
        first = asyncio.ensure_future(call())
        tasks = {first}
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.delay_s)
            if not done:
                if not self.budget.allow() or await self._saturated():
                    return await first, False
                tasks.add(asyncio.ensure_future(call()))
            pending = tasks - done
            last: BaseException | None = None
            while done or pending:
                for t in done:
                    if t.exception() is None:
                        return t.result(), len(tasks) > 1
                    last = t.exception()
                if not pending:
                    break
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            assert last is not None
            raise last
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
