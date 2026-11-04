from datetime import date

from app.rules.eligibility import (
    EligibilityFacts,
    Eligible,
    NotEligible,
    OpenRequest,
    SubjectFact,
    eligibility,
)

T = date(2026, 10, 31)


def f(**kw):
    kw.setdefault("today", T)
    kw.setdefault("sla_hours", 48)
    return EligibilityFacts(**kw)


def sa(service, subject=None, opened=T):
    return OpenRequest(service, subject, "SA", opened)


def test_diploma_graduated_eligible():
    assert eligibility("DOC-040", f(enrollment_status="GRADUATED"), subject=None) == Eligible()


def test_diploma_issued_offers_gen090():
    r = eligibility("DOC-040", f(enrollment_status="GRADUATED", diploma_issued=True), subject=None)
    assert isinstance(r, NotEligible) and r.alternativa == "GEN-090"


def test_doc041_requires_issued_diploma():
    assert isinstance(eligibility("DOC-041", f(), subject=None), NotEligible)
    assert eligibility("DOC-041", f(diploma_issued=True), subject=None) == Eligible()


def test_doc001_active():
    assert eligibility("DOC-001", f(enrollment_status="ACTIVE"), subject=None) == Eligible()
    assert isinstance(
        eligibility("DOC-001", f(enrollment_status="LOCKED"), subject=None), NotEligible
    )


def test_reactivation_locked():
    assert eligibility("ACA-011", f(enrollment_status="LOCKED"), subject=None) == Eligible()
    assert isinstance(
        eligibility("ACA-011", f(enrollment_status="ACTIVE"), subject=None), NotEligible
    )


def test_slip_open_invoice():
    assert eligibility("FIN-030", f(has_open_invoice=True), subject=None) == Eligible()
    assert isinstance(eligibility("FIN-030", f(), subject=None), NotEligible)


def test_renegotiation_overdue():
    assert eligibility("FIN-031", f(overdue_principal=1), subject=None) == Eligible()
    assert isinstance(eligibility("FIN-031", f(), subject=None), NotEligible)


def test_second_call_needs_subject_in_progress():
    facts = f(subjects={"CALC1": SubjectFact("IN_PROGRESS"), "ALG": SubjectFact("APPROVED")})
    r = eligibility("ACA-021", facts, subject=None)
    assert isinstance(r, NotEligible) and r.motivo_pt
    assert eligibility("ACA-021", facts, subject="CALC1") == Eligible()
    assert isinstance(eligibility("ACA-021", facts, subject="ALG"), NotEligible)


def test_grade_review_needs_posted_grade():
    facts = f(subjects={"CALC1": SubjectFact("IN_PROGRESS", True)})
    assert eligibility("ACA-020", facts, subject="CALC1") == Eligible()


def test_aca020_021_by_subject():
    facts = f(
        subjects={"CALC1": SubjectFact("IN_PROGRESS"), "ALG": SubjectFact("IN_PROGRESS")},
        open_requests=(sa("ACA-021", "CALC1"), OpenRequest("ACA-021", None, "secretaria", T)),
    )
    assert isinstance(eligibility("ACA-021", facts, subject="CALC1"), NotEligible)
    assert eligibility("ACA-021", facts, subject="ALG") == Eligible()


def test_rule1_doc_fin_aca011_tec060():
    for svc in ("DOC-001", "FIN-030", "ACA-011", "TEC-060"):
        facts = f(
            enrollment_status="ACTIVE", open_requests=(OpenRequest(svc, None, "secretaria", T),)
        )
        assert isinstance(eligibility(svc, facts, subject=None), NotEligible), svc


def test_sa_older_than_twice_sla_does_not_block():
    old = f(enrollment_status="ACTIVE", open_requests=(sa("DOC-001", opened=date(2026, 10, 20)),))
    assert eligibility("DOC-001", old, subject=None) == Eligible()
    new = f(enrollment_status="ACTIVE", open_requests=(sa("DOC-001", opened=date(2026, 10, 30)),))
    assert isinstance(eligibility("DOC-001", new, subject=None), NotEligible)


def test_other_services_need_enrollment():
    assert eligibility("TEC-060", f(enrollment_status="ACTIVE"), subject=None) == Eligible()
    assert isinstance(eligibility("TEC-060", f(), subject=None), NotEligible)


def test_gen090_ouv099_never_blocked():
    for svc in ("GEN-090", "OUV-099"):
        facts = f(open_requests=(OpenRequest(svc, None, "secretaria", T), sa(svc)))
        assert eligibility(svc, facts, subject=None) == Eligible()
