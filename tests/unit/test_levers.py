import asyncio
from pathlib import Path
from typing import Any

from app.agent import make_llm
from app.agent.levers import HedgedLLM, approved_sentences
from app.agent.llm import LLMResponse, Message, Usage
from app.config import AppSettings


def test_levers_off_by_default(app_secrets: Path) -> None:
    s = AppSettings()
    assert s.lever_hedge_first_call is False and s.lever_sentence_stream is False
    assert not isinstance(make_llm(s), HedgedLLM)


def test_hedge_flag_wraps_provider(app_secrets: Path) -> None:
    s = AppSettings(lever_hedge_first_call=True, llm_provider="gemini", gemini_api_key="k" * 32)
    assert isinstance(make_llm(s), HedgedLLM)


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


def _kw(*msgs: Message) -> dict[str, Any]:
    return dict(model="m", system="s", messages=msgs, tools=(), deadline=1e9)


async def test_hedge_cancels_loser_and_counts_cost() -> None:
    inner = _Slow()
    llm = HedgedLLM(inner, delay_s=0.01)
    r = await llm.generate(**_kw(Message("user", "oi")))
    assert r.text == "r2" and inner.cancelled == [1]
    assert llm.hedges == 1 and llm.hedge_usage == Usage(100, 20)  # a perdedora é cobrada


async def test_hedge_not_fired_when_first_is_fast() -> None:
    class Fast:
        n = 0

        async def generate(self, **_: Any) -> LLMResponse:
            Fast.n += 1
            return LLMResponse("ok", (), Usage(1, 1), "m")

    llm = HedgedLLM(Fast(), delay_s=0.05)
    await llm.generate(**_kw(Message("user", "oi")))
    assert Fast.n == 1 and llm.hedges == 0


async def test_hedge_only_first_call_of_turn() -> None:
    inner = _Slow()
    llm = HedgedLLM(inner, delay_s=0.01)
    tool = Message("tool", tool_name="t", tool_result={})
    inner.n = 1  # a única chamada (n=2) responde logo; sem tool a hedge dispararia
    await llm.generate(**_kw(Message("user", "oi"), tool))
    assert inner.n == 2 and llm.hedges == 0


def test_sentence_stream_only_after_guard() -> None:
    ans = "Você deve R$ 10,00. Vence dia 5! Pague até lá."
    assert approved_sentences(ans, "pass") == [
        "Você deve R$ 10,00.",
        "Vence dia 5!",
        "Pague até lá.",
    ]
    assert approved_sentences(ans, "retry_pass")
    assert approved_sentences(ans, "degraded")
    for g in ("fallback", "n/a", "retry"):
        assert approved_sentences(ans, g) == []
