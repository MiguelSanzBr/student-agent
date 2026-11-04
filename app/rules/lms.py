"""Situação de provas e resumo do LMS (spec §5.5)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Literal

QuizStatus = Literal["aberta", "realizada", "encerrada_sem_tentativa", "futura"]


@dataclass(frozen=True)
class Quiz:
    id: str
    opens: date
    closes: date
    allowed_attempts: int
    counts_for_grade: bool  # False = simulado
    kind: str = "prova"


@dataclass(frozen=True)
class Attempt:
    quiz_id: str


@dataclass(frozen=True)
class NextAssessment:
    date: date
    kind: str
    source: Literal["lms", "calendario"]


@dataclass(frozen=True)
class LmsSummary:
    simulados_abertos: int
    simulados_com_tentativa_restante: int
    proxima_avaliacao: NextAssessment | None
    pendentes: int


def _n_attempts(quiz: Quiz, attempts: Iterable[Attempt]) -> int:
    return sum(a.quiz_id == quiz.id for a in attempts)


def quiz_status(quiz: Quiz, attempts: Iterable[Attempt], today: date) -> QuizStatus:
    if _n_attempts(quiz, attempts) > 0:
        return "realizada"
    if today < quiz.opens:
        return "futura"
    return "aberta" if today <= quiz.closes else "encerrada_sem_tentativa"


def lms_summary(
    quizzes: Iterable[Quiz],
    attempts: Iterable[Attempt],
    today: date,
    *,
    modality: str,
    calendar: Mapping[str, date],
) -> LmsSummary:
    quizzes, attempts = list(quizzes), list(attempts)
    practice = [q for q in quizzes if not q.counts_for_grade]
    graded = [q for q in quizzes if q.counts_for_grade]
    in_window = [q for q in practice if q.opens <= today <= q.closes]
    abertos = sum(_n_attempts(q, attempts) == 0 for q in in_window)
    restante = sum(0 < _n_attempts(q, attempts) < q.allowed_attempts for q in in_window)
    status = {q.id: quiz_status(q, attempts, today) for q in graded}
    todo = sorted(
        (
            (q.opens if status[q.id] == "futura" else q.closes, q)
            for q in graded
            if status[q.id] in ("aberta", "futura")
        ),
        key=lambda t: t[0],
    )
    nxt: NextAssessment | None = (
        NextAssessment(todo[0][0], todo[0][1].kind, "lms") if todo else None
    )
    if nxt is None and modality == "presencial":
        future = sorted((d, k) for k, d in calendar.items() if d >= today)
        if future:
            nxt = NextAssessment(future[0][0], future[0][1], "calendario")
    return LmsSummary(abertos, restante, nxt, sum(s == "aberta" for s in status.values()))
