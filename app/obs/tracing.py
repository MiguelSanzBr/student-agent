from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

SpanKind = Literal["AGENT", "LLM", "TOOL", "GUARDRAIL", "CHAIN"]


class Span:
    """No-op até a T29."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass


@contextmanager
def span(name: str, kind: SpanKind, **attrs: Any) -> Iterator[Span]:
    yield Span()
