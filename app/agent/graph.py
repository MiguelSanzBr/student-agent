"""Só os tipos do turno na T2; o grafo é da T14."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.agent.llm import Message, Usage
from app.tools.base import Domain, DraftCard, EntityRecord, ToolCallTrace

TurnStatus = Literal["ok", "degraded", "fallback", "fixed", "blocked_limit", "shed",
                     "budget_exceeded", "llm_unavailable", "cancelled"]  # fmt: skip
AnswerKind = Literal["resposta", "recusa", "vazio", "fora_cobertura", "transbordo"]


@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: TurnStatus
    guard: Literal["pass", "retry_pass", "degraded", "fallback", "n/a"]
    draft: DraftCard | None
    tool_calls: tuple[ToolCallTrace, ...]
    tools_class: Literal["card", "1", "2+"]  # 0, 1 ou ≥ 2 ferramentas executadas
    llm_calls: int
    model: str
    answer_kind: AnswerKind
    domain: Domain | Literal["cartao", "geral"]
    draft_only: bool
    usage: Usage
    timings_ms: Mapping[str, float]
    first_event_ms: float | None
    pre_guard_answer: str | None
    new_messages: tuple[Message, ...]
    records: tuple[EntityRecord, ...]
