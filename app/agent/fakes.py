import asyncio
import random
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall, ToolChoice, Usage
from app.config import Lognormal, Quota
from app.tools.base import ToolSpec
from core.clock import monotonic


@dataclass(frozen=True)
class Call:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    deadline: float
    tool_choice: ToolChoice = "auto"


class ScriptedLLM:
    """Devolve as respostas do roteiro em ordem; grava cada chamada em `.calls`."""

    def __init__(
        self, script: Sequence[LLMResponse | Callable[[Sequence[Message]], LLMResponse]]
    ) -> None:
        self._script = list(script)
        self.calls: list[Call] = []

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        self.calls.append(Call(model, system, tuple(messages), tuple(tools), deadline, tool_choice))
        if len(self.calls) > len(self._script):
            raise AssertionError("roteiro do ScriptedLLM esgotado")
        step = self._script[len(self.calls) - 1]
        return step(messages) if callable(step) else step


class SmokeLLM(ScriptedLLM):
    """`LLM_PROVIDER=fake`: chama get_financial_summary e copia `total_para_quitar` (make smoke)."""

    def __init__(self) -> None:
        super().__init__([])

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        self.calls.append(Call(model, system, tuple(messages), tuple(tools), deadline, tool_choice))
        last = messages[-1]
        if last.role == "tool" and last.tool_name == "get_financial_summary" and last.tool_result:
            total = last.tool_result["total_para_quitar"]
            return LLMResponse(f"O total para quitar é {total}.", (), Usage(100, 20), model)
        call = ToolCall("smoke-1", "get_financial_summary", {"enrollment": "1"})
        return LLMResponse(None, (call,), Usage(100, 10), model)


P_TOOL, MAX_TOOLS = 0.5, 4  # defaults do LoadTestLLM (evals.latency.load_test deriva a cota deles)


class LoadTestLLM:
    """Provedor falso para teste de carga sem gastar: a latência de cada chamada é sorteada da
    lognormal do componente (`slo_targets.components`) e a cota RPM da janela de 60 s é imposta
    (acima dela: `LLMUnavailable("quota")`, como o 429 do Gemini). Emite ferramenta com
    probabilidade `p_tool` com ferramentas oferecidas, até `max_tools` por turno; com `p_retry`,
    simula o retry transitório do adaptador (métrica e latência)."""

    def __init__(
        self, components: Mapping[str, Lognormal], quota: Quota, *, seed: int,
        p_tool: float = P_TOOL, max_tools: int = MAX_TOOLS, p_retry: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = monotonic,
    ) -> None:  # fmt: skip
        self._c, self._quota, self._rng = components, quota, random.Random(seed)
        self._p_tool, self._max_tools, self._p_retry = p_tool, max_tools, p_retry
        self._sleep, self._clock = sleep, clock
        self._stamps: deque[float] = deque()

    def sample(self, component: str) -> float:
        c = self._c[component]
        return self._rng.lognormvariate(c.mu, c.sigma)

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= 60:
            self._stamps.popleft()
        if len(self._stamps) >= self._quota.rpm:
            raise LLMUnavailable("quota")
        self._stamps.append(now)
        done = 0
        for m in reversed(messages):
            if m.role == "user":
                break
            done += m.role == "tool"
        readable = [t for t in tools if not t.parameters.get("required")]  # args {} são válidos
        emit_tool = bool(readable) and done < self._max_tools and self._rng.random() < self._p_tool
        comp = "tool_call" if emit_tool else "answer_call"
        lat = self.sample(comp)
        if self._rng.random() < self._p_retry:  # como o GeminiProvider: 1 retry transitório
            from app.obs.metrics import METRICS

            METRICS["agent_llm_retries_total"].inc()  # type: ignore[union-attr]
            lat += self.sample(comp)
        left = deadline - self._clock()
        if lat > left:  # como o provedor real: o prazo corta a chamada
            await self._sleep(max(0.0, left))
            raise LLMUnavailable("timeout")
        await self._sleep(lat)
        if emit_tool:
            tool = self._rng.choice(readable)
            args = (
                {"enrollment": "1"} if "enrollment" in tool.parameters.get("properties", {}) else {}
            )
            call = ToolCall(f"load-{self._rng.getrandbits(32):08x}", tool.name, args)
            return LLMResponse(None, (call,), Usage(3500, 60, 100), "loadtest")
        return LLMResponse("Resposta de carga.", (), Usage(4000, 120, 300), "loadtest")
