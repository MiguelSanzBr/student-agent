"""Métricas da spec §9.4; buckets vêm do slo_targets.yaml.

Multiprocesso: com PROMETHEUS_MULTIPROC_DIR o prometheus_client grava por processo e `render` soma.
"""

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.multiprocess import MultiProcessCollector

from app.config import load_slo_targets

_B = load_slo_targets().buckets


def _c(name: str, doc: str, *labels: str) -> Counter:
    return Counter(name.removesuffix("_total"), doc, labels)


def _h(name: str, doc: str, *labels: str) -> Histogram:
    return Histogram(name, doc, labels, buckets=_B)


METRICS: dict[str, Counter | Gauge | Histogram] = {
    "agent_turns_total": _c("agent_turns_total", "Turnos", "status", "channel"),
    "agent_turn_seconds": _h(
        "agent_turn_seconds", "Latência do turno", "tools_class", "llm_calls", "guard", "model"
    ),  # fmt: skip
    "agent_first_event_seconds": _h("agent_first_event_seconds", "TTFE", "tools_class"),
    "agent_llm_seconds": _h("agent_llm_seconds", "Latência do LLM"),
    "agent_llm_errors_total": _c("agent_llm_errors_total", "Erros do LLM", "kind"),
    "agent_llm_retries_total": _c("agent_llm_retries_total", "Retries do LLM"),
    "agent_admission_rejected_total": _c(
        "agent_admission_rejected_total", "Admissões recusadas", "reason"
    ),
    "agent_turn_slots_in_use": Gauge(
        "agent_turn_slots_in_use", "Vagas de turno em uso", multiprocess_mode="livesum"
    ),  # fmt: skip
    "agent_guard_total": _c("agent_guard_total", "Desfechos do guard", "outcome"),
    "agent_answer_kind_total": _c("agent_answer_kind_total", "Tipos de resposta", "kind", "domain"),
    "agent_draft_total": _c("agent_draft_total", "Funil do rascunho", "outcome"),
    "agent_tokens_total": _c("agent_tokens_total", "Tokens", "kind"),
    "agent_cost_usd_total": _c("agent_cost_usd_total", "Custo em US$"),
    "agent_feedback_total": _c("agent_feedback_total", "Feedback", "useful"),
}


def render(multiproc_dir: str | None = None) -> bytes:
    if not multiproc_dir:
        return generate_latest(REGISTRY)
    reg = CollectorRegistry()
    MultiProcessCollector(reg, path=multiproc_dir)  # type: ignore[no-untyped-call]
    return generate_latest(reg)
