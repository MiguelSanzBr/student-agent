from datetime import UTC, date, datetime

from app.rules.coverage import coverage_note
from app.tools.base import default_ontology, norm, orientation
from app.tools.protocols import _resolve_subject, _secretary

ROWS = [  # (código, nome PT, período, resultado, nota lançada)
    ("ADS0102", "Algoritmos e Programação I", "2026/1", "APPROVED", True),
    ("ADS0404", "Algoritmos e Programação II", "2026/1", "APPROVED", True),
    ("ADS0503", "Engenharia de Software II", "2026/2", "IN_PROGRESS", True),
    ("ADS0203", "Engenharia de Software I", "2026/1", "APPROVED", True),
    ("PED0602", "Política Educacional", "2026/2", "IN_PROGRESS", False),
    ("PED0702", "Psicologia da Educação II", "2026/2", "IN_PROGRESS", False),
]
AMBIGUOUS = "Mais de uma disciplina combina com esse nome; diga o nome completo."


def test_ambiguous_in_past_terms_keeps_ambiguity_message() -> None:
    assert _resolve_subject("algoritmos", ROWS, "2026/2") == (None, AMBIGUOUS)


def test_current_term_breaks_tie_and_two_current_stay_ambiguous() -> None:
    assert _resolve_subject("engenharia", ROWS, "2026/2") == ("ADS0503", None)
    assert _resolve_subject("p", ROWS, "2026/2") == (None, AMBIGUOUS)
    assert _resolve_subject("calculo", ROWS, "2026/2")[1].startswith("Não encontrei")  # type: ignore[union-attr]


def test_unknown_service_code_does_not_break_row() -> None:
    row = ("20260000001", "XXX-999", "Open", datetime(2026, 10, 31, 22, tzinfo=UTC), "aluno")
    out = _secretary(row)
    assert out["servico"] == "Serviço da secretaria" and out["protocolo"] == "20260000001"
    assert out["prazo"] == "não informado"


def test_orientation_phrases_whole_days_and_hours() -> None:
    assert orientation("GEN-090")["prazo"] == "3 dias"
    assert "prazo de 3 dias" in orientation("GEN-090")["orientacao"]
    assert orientation("DOC-001")["prazo"] == "2 dias"
    onto = default_ontology()
    hours = {s: onto.service(s).sla_hours for s in onto.service_codes()}
    assert all(
        orientation(s)["prazo"]
        == (f"{h // 24} dias" if h % 24 == 0 and h != 24 else "1 dia" if h == 24 else f"{h} horas")
        for s, h in hours.items()
    )
    assert norm("Ação") == "acao"


def test_coverage_note_grammar() -> None:
    d = date(2022, 3, 1)
    assert coverage_note("secretaria", d) == "Não há dados da secretaria antes de 01/03/2022."
    assert coverage_note("matricula", d) == "Não há dados da matrícula antes de 01/03/2022."
    assert coverage_note("notas", d) == "Não há dados das notas antes de 01/03/2022."
    assert coverage_note("lms", d) == "Não há dados do AVA antes de 01/03/2022."
    assert coverage_note("SAG", d) == "Não há dados do SAG antes de 01/03/2022."
