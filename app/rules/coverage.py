"""Nota de cobertura de dados."""

from datetime import date

from core.format import fmt_date

_LABEL = {
    "lms": "do AVA",
    "financeiro": "do financeiro",
    "notas": "das notas",
    "secretaria": "da secretaria",
    "matricula": "da matrícula",
}


def coverage_note(kind: str, since: date) -> str:
    return f"Não há dados {_LABEL.get(kind, f'do {kind}')} antes de {fmt_date(since)}."
