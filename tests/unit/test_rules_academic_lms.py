from datetime import date

from app.rules.academic import Progress, graduation_forecast
from app.rules.commitments import commitment_sources
from app.rules.coverage import coverage_note
from app.rules.lms import Attempt, NextAssessment, Quiz, lms_summary, quiz_status
from app.rules.protocols import protocol_status_pt, sla_due
from app.tools.base import EntityRecord

T = date(2026, 10, 31)
CAL = {"AV1": date(2026, 9, 5), "AV2": date(2026, 12, 5)}


def q(id, opens, closes, graded=False, allowed=3):
    return Quiz(id, opens, closes, allowed, graded, "AV2" if graded else "simulado")


def test_forecast():
    out = graduation_forecast(Progress(6, 8), "2026/2")
    assert "se cursar regularmente" in out and out.startswith("2027/1")


def test_quiz_status():
    a = [Attempt("x")]
    assert quiz_status(q("x", date(2026, 10, 1), date(2026, 10, 5)), a, T) == "realizada"
    assert (
        quiz_status(q("y", date(2026, 10, 1), date(2026, 10, 5)), a, T) == "encerrada_sem_tentativa"
    )
    assert quiz_status(q("y", date(2026, 10, 1), date(2026, 11, 5)), a, T) == "aberta"
    assert quiz_status(q("y", date(2026, 11, 1), date(2026, 11, 5)), a, T) == "futura"


def test_open_practice_definition():
    quizzes = [
        q("a", date(2026, 10, 1), date(2026, 11, 5)),
        q("b", date(2026, 10, 1), date(2026, 11, 5)),
    ]
    s = lms_summary(quizzes, [Attempt("b")], T, modality="ead", calendar={})
    assert (s.simulados_abertos, s.simulados_com_tentativa_restante) == (1, 2)


def test_presencial_next_assessment_from_calendar():
    s = lms_summary([], [], T, modality="presencial", calendar=CAL)
    assert s.proxima_avaliacao == NextAssessment(date(2026, 12, 5), "AV2", "calendario")
    assert lms_summary([], [], T, modality="ead", calendar=CAL).proxima_avaliacao is None


def test_lms_next_assessment_wins():
    s = lms_summary(
        [q("p", date(2026, 11, 20), date(2026, 11, 25), graded=True)],
        [],
        T,
        modality="presencial",
        calendar=CAL,
    )
    assert s.proxima_avaliacao == NextAssessment(date(2026, 11, 20), "AV2", "lms")


def test_protocols():
    assert sla_due(date(2026, 10, 31), 48) == date(2026, 11, 2)
    assert protocol_status_pt("Pending Requester") == "aguardando você"


def test_commitment_sources_active_agreement_only():
    rec = lambda s: EntityRecord("acordo", "AG1", ("AG1",), {"status": s, "parcelas": "10"})  # noqa: E731
    assert commitment_sources([rec("rompido")]) == frozenset()
    assert "parcelamento 10x" in commitment_sources([rec("ativo")])
    fin031 = EntityRecord(
        "servico", "FIN-031", (), {"orientacao": "parcelamento em até 12x, conforme análise"}
    )
    assert "parcelamento 12x" in commitment_sources([fin031])


def test_coverage_note():
    assert coverage_note("lms", date(2023, 1, 15)) == "Não há dados do AVA antes de 15/01/2023."
