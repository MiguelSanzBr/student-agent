"""Gravação e replay das respostas do LLM, para rodar a demo sem GEMINI_API_KEY (spec D19)."""

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.agent.llm import LLMProvider, LLMResponse, Message, ToolCall, Usage
from app.tools.base import ToolSpec

DEFAULT_REPLAY = Path(__file__).resolve().parents[2] / "evals" / "replays" / "demo.jsonl"
UNKNOWN_TEXT = "Modo demonstração: escolha uma das perguntas sugeridas."


def _turn_key(messages: Sequence[Message]) -> tuple[str, int]:
    """(pergunta, passo): a pergunta é a última do usuário; o passo, quantas chamadas do
    assistente já houve depois dela (0 = primeira chamada do turno)."""
    last = max((i for i, m in enumerate(messages) if m.role == "user"), default=-1)
    question = (messages[last].text or "") if last >= 0 else ""
    return question, sum(1 for m in messages[last + 1 :] if m.role == "assistant")


def _norm(question: str) -> str:
    return " ".join(question.split()).casefold()


class RecordingLLM:
    """Repassa ao LLM real e acrescenta cada resposta em `path` (JSONL)."""

    def __init__(self, inner: LLMProvider, path: Path) -> None:
        self._inner = inner
        self._path = path

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
    ) -> LLMResponse:  # fmt: skip
        resp = await self._inner.generate(
            model=model, system=system, messages=messages, tools=tools, deadline=deadline
        )  # fmt: skip
        question, step = _turn_key(messages)
        line: dict[str, Any] = {
            "pergunta": question, "passo": step, "text": resp.text,
            "tool_calls": [{"id": c.id, "name": c.name, "args": dict(c.args)}
                           for c in resp.tool_calls],
            "usage": asdict(resp.usage), "model": resp.model, "finish_reason": resp.finish_reason,
        }  # fmt: skip
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        return resp


class ReplayLLM:
    """Devolve a resposta gravada para (pergunta, passo); pergunta fora do roteiro recebe
    `UNKNOWN_TEXT`."""

    def __init__(self, path: Path) -> None:
        self._by_key: dict[tuple[str, int], LLMResponse] = {}
        self._suggested: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            rec = json.loads(raw)
            resp = LLMResponse(
                rec["text"],
                tuple(ToolCall(c["id"], c["name"], c["args"]) for c in rec["tool_calls"]),
                Usage(**rec["usage"]), rec["model"], rec["finish_reason"],
            )  # fmt: skip
            self._by_key[(_norm(rec["pergunta"]), rec["passo"])] = resp
            if rec["passo"] == 0 and rec["pergunta"] not in self._suggested:
                self._suggested.append(rec["pergunta"])

    def suggested(self) -> list[str]:
        """Perguntas que abrem um turno gravado, na ordem do arquivo."""
        return list(self._suggested)

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
    ) -> LLMResponse:  # fmt: skip
        question, step = _turn_key(messages)
        found = self._by_key.get((_norm(question), step))
        if found is not None:
            return found
        return LLMResponse(UNKNOWN_TEXT, (), Usage(), model, "STOP")
