"""Alavancas de latência, todas atrás de flag desligada (spec §6.5; medir antes de ligar)."""

import asyncio
import re
from collections.abc import Sequence
from typing import Any

from app.agent.llm import LLMProvider, LLMResponse, Message, Usage
from app.tools.base import ToolSpec

APPROVED = frozenset({"pass", "retry_pass", "degraded"})  # desfechos do guard que liberam texto
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class HedgedLLM:
    """`lever_hedge_first_call`: na 1ª chamada do turno (sem resultado de ferramenta ainda), se não
    houver resposta em `delay_s` (p90 da chamada), manda uma 2ª requisição idêntica. A 1ª que
    terminar bem vence; a outra é cancelada, mas é cobrada: `hedge_usage` soma, por disparo, o
    uso da vencedora (a perdedora cancelada não devolve o seu)."""

    def __init__(self, inner: LLMProvider, delay_s: float) -> None:
        self._inner, self._delay = inner, delay_s
        self.hedges = 0
        self.hedge_usage = Usage()

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
    ) -> LLMResponse:  # fmt: skip
        kw: dict[str, Any] = dict(model=model, system=system, messages=messages, tools=tools,
                                  deadline=deadline)  # fmt: skip
        if any(m.role == "tool" for m in messages):
            return await self._inner.generate(**kw)
        first = asyncio.ensure_future(self._inner.generate(**kw))
        tasks = {first}
        try:
            done, _ = await asyncio.wait(tasks, timeout=self._delay)
            if not done:
                self.hedges += 1
                tasks.add(asyncio.ensure_future(self._inner.generate(**kw)))
            pending = tasks - done
            last: BaseException | None = None
            while done or pending:
                for t in done:
                    if t.exception() is None:
                        r = t.result()
                        if len(tasks) > 1:
                            self._charge(r.usage)
                        return r
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

    def _charge(self, u: Usage) -> None:
        h = self.hedge_usage
        self.hedge_usage = Usage(
            h.input_tokens + u.input_tokens, h.output_tokens + u.output_tokens,
            h.thinking_tokens + u.thinking_tokens, h.cached_tokens + u.cached_tokens,
        )  # fmt: skip


def approved_sentences(answer: str, guard: str) -> list[str]:
    """`lever_sentence_stream`: frases para `answer_delta`. Só depois do desfecho do guard e só
    se ele aprovou (pass, retry_pass, degraded); em fallback ou sem guard, nada sai."""
    if guard not in APPROVED:
        return []
    return [s for s in _SENTENCE.split(answer.strip()) if s]
