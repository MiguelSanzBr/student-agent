from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.agent.llm import LLMResponse, Message, ToolCall, Usage
from app.tools.base import ToolSpec


@dataclass(frozen=True)
class Call:
    model: str
    system: str
    messages: tuple[Message, ...]
    tools: tuple[ToolSpec, ...]
    deadline: float


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
    ) -> LLMResponse:  # fmt: skip
        self.calls.append(Call(model, system, tuple(messages), tuple(tools), deadline))
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
    ) -> LLMResponse:  # fmt: skip
        self.calls.append(Call(model, system, tuple(messages), tuple(tools), deadline))
        last = messages[-1]
        if last.role == "tool" and last.tool_name == "get_financial_summary" and last.tool_result:
            total = last.tool_result["total_para_quitar"]
            return LLMResponse(f"O total para quitar é {total}.", (), Usage(100, 20), model)
        call = ToolCall("smoke-1", "get_financial_summary", {"enrollment": "1"})
        return LLMResponse(None, (call,), Usage(100, 10), model)
