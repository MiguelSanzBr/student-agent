import json
from typing import get_args

import pytest

from app.agent.graph import AnswerKind, TurnResult, TurnStatus
from app.config import load_slo_targets
from app.obs.metrics import METRICS
from app.tools.base import DraftCard, DraftKey, EntityRecord, ToolOutput, find_entity, index_records
from app.tools.registry import TOOL_NAMES, build_tools
from tests.fixtures.entity_records import SAMPLE_RECORDS

FORBIDDEN = {"cpf", "ra", "aluno", "student", "person", "email"}


def test_registry_11_identical_specs() -> None:
    tools = build_tools()
    assert [t.spec.name for t in tools] == list(TOOL_NAMES) and len(tools) == 11
    assert json.dumps([t.spec for t in build_tools()], default=vars) == json.dumps(
        [t.spec for t in tools], default=vars
    )
    for t in tools:
        p = t.spec.parameters
        assert p["additionalProperties"] is False
        assert not FORBIDDEN & set(p["properties"])
        if "enrollment" in p["properties"]:
            assert p["properties"]["enrollment"]["enum"] == ["1", "2", "3"]


def test_draftcard_public_no_dead_token() -> None:
    assert "token" not in DraftCard.__dataclass_fields__
    card = DraftCard(
        "d1",
        DraftKey("DOC-001", None),
        "Declaração",
        None,
        "R$ 0,00",
        "10/11/2026",
        "x",
        "y",
        "conv-1",
        "2",
    )
    pub = card.public()
    assert not {"conversation_id", "enrollment_index"} & set(pub)
    assert not {"conv-1", "2"} & set(pub.values())


def test_entity_record_key_and_lookup() -> None:
    assert EntityRecord("disciplina", "CALC1", ("Cálculo I",), {}).key == "disciplina:CALC1"
    idx = index_records(SAMPLE_RECORDS)
    r = find_entity(idx, "calculo i")
    assert r is not None and r.id == "CALC1"
    assert find_entity(idx, "Física II") is None


def test_tool_output_rejects_untyped_records() -> None:
    with pytest.raises(TypeError):
        ToolOutput(data={}, records=({"kind": "x"},))  # type: ignore[arg-type]


def test_metrics_from_slo_targets() -> None:
    names = {
        "agent_turns_total",
        "agent_turn_seconds",
        "agent_first_event_seconds",
        "agent_llm_seconds",
        "agent_llm_errors_total",
        "agent_llm_retries_total",
        "agent_admission_rejected_total",
        "agent_turn_slots_in_use",
        "agent_guard_total",
        "agent_answer_kind_total",
        "agent_draft_total",
        "agent_tokens_total",
        "agent_cost_usd_total",
        "agent_llm_thinking_dropped_total",
        "agent_feedback_total",
    }
    assert set(METRICS) == names
    buckets = load_slo_targets().buckets
    for n in ("agent_turn_seconds", "agent_first_event_seconds", "agent_llm_seconds"):
        assert list(METRICS[n]._upper_bounds[:-1]) == buckets  # type: ignore[attr-defined]


def test_turnresult_literals() -> None:
    assert set(get_args(TurnResult.__annotations__["tools_class"])) == {"card", "1", "2+"}
    assert "cancelled" in get_args(TurnStatus)
    assert len(get_args(AnswerKind)) == 5


def test_turnresult_flag_defaults() -> None:
    f = TurnResult.__dataclass_fields__
    for n in ("became_tainted", "offered_other_enrollment", "cost_degraded"):
        assert f[n].default is False
