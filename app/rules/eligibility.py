"""Elegibilidade para abrir protocolo (spec §5.5), na ordem da spec."""

from dataclasses import dataclass, field
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class OpenRequest:
    service: str
    subject: str | None
    source: Literal["secretaria", "SA"]
    opened: date


@dataclass(frozen=True)
class SubjectFact:
    status: str  # APPROVED | IN_PROGRESS | ...
    has_grade_current_term: bool = False


@dataclass(frozen=True)
class EligibilityFacts:
    today: date
    sla_hours: int = 0
    enrollment_status: str | None = None
    diploma_issued: bool = False
    has_open_invoice: bool = False
    overdue_principal: int = 0
    subjects: dict[str, SubjectFact] = field(default_factory=dict)
    open_requests: tuple[OpenRequest, ...] = ()


@dataclass(frozen=True)
class Eligible:
    pass


@dataclass(frozen=True)
class NotEligible:
    motivo_pt: str
    alternativa: str | None = None


_NEVER_BLOCKED = {"GEN-090", "OUV-099"}
_BY_SUBJECT = {"ACA-020", "ACA-021"}


def _blocked(service: str, facts: EligibilityFacts, subject: str | None) -> bool:
    if service in _NEVER_BLOCKED:
        return False
    by_subject = service in _BY_SUBJECT
    if not (
        by_subject or service.startswith(("DOC-", "FIN-")) or service in {"ACA-011", "TEC-060"}
    ):
        return False
    for r in facts.open_requests:
        if r.service != service:
            continue
        if by_subject:  # a secretaria não tem disciplina estruturada: só SA- da mesma disciplina
            if r.source != "SA" or r.subject != subject:
                continue
        if r.source == "SA" and (facts.today - r.opened).days >= 2 * facts.sla_hours / 24:
            continue  # SA- com mais de 2x o SLA deixa de bloquear
        return True
    return False


def eligibility(
    service: str, facts: EligibilityFacts, *, subject: str | None
) -> Eligible | NotEligible:
    if service in _BY_SUBJECT and subject is None:
        return NotEligible("Preciso saber de qual disciplina é o pedido.")
    if _blocked(service, facts, subject):
        return NotEligible("Já existe um protocolo aberto para este serviço.")
    st = facts.enrollment_status
    no = NotEligible
    match service:
        case "DOC-040":
            if st != "GRADUATED":
                return no("A emissão de diploma é para matrículas concluídas.")
            if facts.diploma_issued:
                return no("O diploma já foi emitido.", "GEN-090")
        case "DOC-041":
            if not facts.diploma_issued:
                return no("A 2ª via exige diploma já emitido.")
        case "DOC-001":
            if st != "ACTIVE":
                return no("A declaração de matrícula é para matrículas ativas.")
        case "ACA-011":
            if st != "LOCKED":
                return no("A reativação é para matrículas trancadas.")
        case "ACA-020":
            s = facts.subjects.get(subject or "")
            if s is None or not s.has_grade_current_term:
                return no("A disciplina não tem nota lançada no semestre corrente.")
        case "ACA-021":
            s = facts.subjects.get(subject or "")
            if s is None or s.status != "IN_PROGRESS":
                return no("A disciplina não está em andamento no semestre corrente.")
        case "FIN-030":
            if not facts.has_open_invoice:
                return no("Não há fatura em aberto.")
        case "FIN-031":
            if facts.overdue_principal <= 0:
                return no("Não há valor vencido para renegociar.")
        case _:
            if st is None and service not in _NEVER_BLOCKED:
                return no("Não encontrei matrícula para este pedido.")
    return Eligible()
