from collections.abc import Callable, Sequence
from dataclasses import dataclass

from app.agent.llm import LLMResponse, Message
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
