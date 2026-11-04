"""Nota de cobertura de dados."""

from datetime import date

from core.format import fmt_date

_LABEL = {
    "lms": "AVA",
    "financeiro": "financeiro",
    "notas": "notas",
    "secretaria": "secretaria",
    "matricula": "matrícula",
}


def coverage_note(kind: str, since: date) -> str:
    return f"Não há dados do {_LABEL.get(kind, kind)} antes de {fmt_date(since)}."
