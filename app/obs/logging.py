"""structlog JSON (stdout) com `trace_id`; sem PII (chaves sensíveis somem, padrões mascarados)."""

import logging
import re
import sys
from typing import Any

import structlog
from opentelemetry import trace

from app.config import AppSettings

_DENY = re.compile(
    r"cpf|nome|name|texto|text|validation|token(?!s)|password|secret|authorization|cookie|answer|email",
    re.I,
)
_MASK = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b|Bearer\s+\S+|eyJ[\w-]+\.[\w-]+\.[\w-]+")


def _trace_id(_: Any, __: str, ed: dict[str, Any]) -> dict[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        ed["trace_id"] = format(ctx.trace_id, "032x")
    return ed


def _scrub(_: Any, __: str, ed: dict[str, Any]) -> dict[str, Any]:
    # exceção: só o tipo (a mensagem e o traceback podem carregar PII)
    exc = ed.pop("exc_info", None)
    if not exc and (rec := ed.get("_record")) is not None:
        exc = rec.exc_info
    if isinstance(exc, tuple) and exc[0] is not None:
        ed["exception_type"] = exc[0].__name__
    elif isinstance(exc, BaseException):
        ed["exception_type"] = type(exc).__name__
    out = {k: v for k, v in ed.items() if not _DENY.search(k) or k == "event"}
    return {k: _MASK.sub("[removido]", v) if isinstance(v, str) else v for k, v in out.items()}


def configure_logging(settings: AppSettings) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars, structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True), _trace_id, _scrub,
    ]  # fmt: skip
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger, cache_logger_on_first_use=False,
    )  # fmt: skip
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer()],
    ))  # fmt: skip
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
