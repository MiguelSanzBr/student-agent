"""Atribuição automática de causa de erro pelo trace (analysis_plan.md §11)."""

from __future__ import annotations

from typing import Literal

from evals.golden.score import DatasetItem, ScoreResult, TurnRecord, is_refusal, score_item
from evals.stats import TurnTrace

Cause = Literal["roteamento", "dados", "redacao", "guard_bloqueou_certa", "recusa_indevida",
                "gabarito"]  # fmt: skip


def attribute_cause(item: DatasetItem, trace: TurnTrace, score: ScoreResult) -> Cause:
    """Primeira regra que casa; `score` é o resultado (reprovado) do item."""
    check = item.steps[trace.step]
    if trace.gabarito_marked:
        return "gabarito"
    if trace.pre_guard_answer is not None:
        pre = TurnRecord(trace.pre_guard_answer, trace.tools_called)
        if not [r for r in score_item(DatasetItem(item.id, item.template_id, item.category,
                item.split, (check,)), [pre]).reasons if "rascunho" not in r]:  # fmt: skip
            return "guard_bloqueou_certa"
    if is_refusal(trace.answer) and not check.expect_refusal:
        return "recusa_indevida"
    if any(t not in trace.tools_called for t in check.expected_tools):
        return "roteamento"
    if any(trace.tool_outputs.get(k) != v for k, v in trace.oracle_outputs.items()):
        return "dados"
    return "redacao"
