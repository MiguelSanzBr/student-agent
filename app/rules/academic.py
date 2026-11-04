"""Regras acadêmicas puras."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Progress:
    completed_terms: int
    total_terms: int


def _advance(term: str, n: int) -> str:
    year, sem = term.split("/")
    idx = int(year) * 2 + (int(sem) - 1) + n
    return f"{idx // 2}/{idx % 2 + 1}"


def graduation_forecast(progress: Progress, current_term: str) -> str:
    remaining = max(1, progress.total_terms - progress.completed_terms)  # o semestre corrente conta
    return f"{_advance(current_term, remaining - 1)}, se cursar regularmente"
