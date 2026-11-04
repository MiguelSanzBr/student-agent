"""Único lugar autorizado a ler relógio e data de referência (ver test_clock_rule)."""

import time
from datetime import UTC, date, datetime
from typing import Protocol


class _HasReferenceDate(Protocol):
    reference_date: date


def reference_today(settings: _HasReferenceDate) -> date:
    return settings.reference_date


def wall_now() -> datetime:
    return datetime.now(UTC)


def monotonic() -> float:
    return time.monotonic()
