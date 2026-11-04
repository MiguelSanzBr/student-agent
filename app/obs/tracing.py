"""Spans OpenTelemetry com atributos OpenInference, exportados ao Phoenix (spec §9.4).

Sem conteúdo por padrão: texto do aluno e da resposta só entram em dev+eval (`set_content`).
Exceção registrada só pelo tipo; nenhum evento `exception` (a mensagem pode ter PII).
"""

import hashlib
import hmac
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode

from app.config import AppSettings
from app.obs import annotations

SpanKind = Literal["AGENT", "LLM", "TOOL", "GUARDRAIL", "CHAIN"]
_tracer: trace.Tracer = trace.NoOpTracer()
_capture = False


class Span:
    def __init__(self, s: trace.Span) -> None:
        self._s = s

    def set_attribute(self, key: str, value: Any) -> None:
        self._s.set_attribute(key, value)

    def set_content(self, key: str, value: str) -> None:
        """Conteúdo (texto do aluno/da resposta): só com otel_capture_content (dev+eval)."""
        if _capture:
            self._s.set_attribute(key, value)

    @property
    def trace_id(self) -> str:
        ctx = self._s.get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else ""


def setup_tracing(settings: AppSettings, exporter: SpanExporter | None = None) -> TracerProvider:
    """Cria o provider (não toca o global do OTel). `exporter` só nos testes (processo síncrono)."""
    global _tracer, _capture
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "student-agent",
                "openinference.project.name": settings.phoenix_project,
            }
        )  # fmt: skip
    )
    endpoint = settings.otel_exporter_otlp_endpoint
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        base = endpoint.removesuffix("/v1/traces")
        annotations.configure(
            flush=lambda: provider.force_flush(2000), send=annotations.phoenix_sender(base)
        )
    _tracer, _capture = provider.get_tracer("student-agent"), settings.otel_capture_content
    return provider


def session_id(settings: AppSettings, conversation_id: str) -> str:
    return hmac.new(
        settings.hmac_key.encode(), conversation_id.encode(), hashlib.sha256
    ).hexdigest()[:32]


def sha12(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:12]


def current_span() -> Span:
    return Span(trace.get_current_span())


def llm_attrs(sp: Span, model: str, inp: int, out: int, thinking: int, cached: int) -> None:
    for k, v in {
        "llm.model_name": model, "gen_ai.request.model": model,
        "llm.token_count.prompt": inp, "llm.token_count.completion": out + thinking,
        "llm.token_count.prompt_details.cache_read": cached,
        "llm.token_count.completion_details.reasoning": thinking,
        "gen_ai.usage.input_tokens": inp, "gen_ai.usage.output_tokens": out + thinking,
    }.items():  # fmt: skip
        sp.set_attribute(k, v)


@contextmanager
def span(name: str, kind: SpanKind, **attrs: Any) -> Iterator[Span]:
    with _tracer.start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as s:
        s.set_attribute("openinference.span.kind", kind)
        for k, v in attrs.items():
            s.set_attribute(k, v)
        if kind == "AGENT" and s.get_span_context().is_valid:
            ctx = s.get_span_context()
            annotations.remember_root(format(ctx.trace_id, "032x"), format(ctx.span_id, "016x"))
        try:
            yield Span(s)
        except BaseException as e:
            s.set_attribute("exception.type", type(e).__name__)
            s.set_status(Status(StatusCode.ERROR))
            raise
