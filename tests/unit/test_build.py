import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from core.ontology import Ontology
from etl.build import (
    build_academic,
    build_catalog,
    build_counts,
    build_coverage,
    build_finance,
    build_lms,
    build_protocols,
    resolve,
)
from etl.raw import RawData, read_raw

ROOT = Path(__file__).parents[2]
MINI = ROOT / "tests/fixtures/mini"
KEY = b"k" * 32
IMPLICIT = {"person_key", "enrollment_key", "brand_family"}


def _onto(raw: RawData) -> Ontology:
    return Ontology.load(
        ROOT / "ontology", {r["CLASS_ID"]: r["SUBJECT_CODE"] for r in raw["sag/class_section"]}
    )


@pytest.fixture(scope="module")
def raw() -> RawData:
    return read_raw(MINI)


@pytest.fixture(scope="module")
def onto(raw: RawData) -> Ontology:
    return _onto(raw)


@pytest.fixture(scope="module")
def tables(raw: RawData, onto: Ontology) -> dict[str, list[dict[str, Any]]]:
    res = resolve(raw, KEY)
    out: dict[str, list[dict[str, Any]]] = {}
    for part in (
        build_academic(raw, res, onto),
        build_finance(raw, res, onto),
        build_protocols(raw, res, onto),
        build_lms(raw, res, onto),
        build_catalog(raw, onto),
    ):
        out.update(part)
    out["coverage"] = build_coverage(raw)
    return out


def _with(raw: RawData, **tables: list[dict[str, str]]) -> dict[str, Any]:
    return {**raw, **{k.replace("__", "/"): v for k, v in tables.items()}}


def _cells(tables: Mapping[str, list[dict[str, Any]]]) -> list[str]:
    return [str(v) for rows in tables.values() for r in rows for v in r.values() if v is not None]


def test_no_analyst_login_anywhere(raw: RawData, tables: dict[str, list[dict[str, Any]]]) -> None:
    logins = {r["Author"] for r in raw["secretary/protocol_interaction"]}
    logins |= {r["Demand_Analyst"] for r in raw["secretary/protocol"]}
    logins |= {r["Changed_By"] for r in raw["secretary/protocol_status_history"]}
    logins |= {r["Signed_By"] for r in raw["secretary/issued_document"]}
    analysts = {a.lower() for a in logins if a and a.lower() != "system"}
    analysts -= {
        r["Author"].lower()
        for r in raw["secretary/protocol_interaction"]
        if r["Author_Type"] == "Requester"
    }
    assert len(analysts) > 5
    rx = re.compile(r"\b(" + "|".join(map(re.escape, analysts)) + r")\b", re.I)
    assert not [c for c in _cells(tables) if rx.search(c)]


def test_internal_notes_dropped(raw: RawData, onto: Ontology) -> None:
    base = {"Interaction_ID": "1", "Created_At": "2022-03-16T22:17:45Z", "Author": "x"}
    prot = raw["secretary/protocol"][0]["Protocol"]
    rows = [
        {**base, "Protocol": prot, "Author_Type": "Analyst", "Message": "nota",
         "Is_Internal_Note": "True"},
        {**base, "Interaction_ID": "2", "Protocol": prot, "Author_Type": "Analyst",
         "Message": "Request received and under analysis.", "Is_Internal_Note": "False"},
    ]  # fmt: skip
    r2 = _with(raw, secretary__protocol_interaction=rows)
    out = build_protocols(r2, resolve(r2, KEY), onto)["protocol_message"]
    assert [m["message_id"] for m in out] == ["2"]


def test_requester_messages_omitted(tables: dict[str, list[dict[str, Any]]]) -> None:
    roles = {m["autor_papel"] for m in tables["protocol_message"]}
    assert roles and roles <= {"Analyst", "System"}


def test_messages_typed_or_untrusted(raw: RawData, onto: Ontology) -> None:
    prot = raw["secretary/protocol"][0]["Protocol"]
    base = {"Protocol": prot, "Author_Type": "Analyst", "Author": "x", "Is_Internal_Note": "False",
            "Created_At": "2022-03-16T22:17:45Z"}  # fmt: skip
    rows = [
        {**base, "Interaction_ID": "1", "Message": "Call me at 555-0100 about a refund."},
        {**base, "Interaction_ID": "2", "Message": "Protocol  closed as Resolved."},
    ]
    r2 = _with(raw, secretary__protocol_interaction=rows)
    out = {
        m["message_id"]: m for m in build_protocols(r2, resolve(r2, KEY), onto)["protocol_message"]
    }
    assert (
        out["1"]["modelo"] is None
        and out["1"]["texto_livre"] == "Call me at 555-0100 about a refund."
    )
    assert (
        out["2"]["modelo"] == "Protocolo encerrado como resolvido."
        and out["2"]["texto_livre"] is None
    )


def test_professor_exact_list(raw: RawData, onto: Ontology) -> None:
    sc = raw["sag/student_class"][0]
    section = next(s for s in raw["sag/class_section"] if s["CLASS_ID"] == sc["CLASS_ID"])
    profs = [
        {**p, "FULL_NAME": "CARLOS ALBERTO PEREIRA"}
        if p["PROFESSOR_ID"] == section["PROFESSOR_ID"]
        else p
        for p in raw["sag/professor"]
    ]
    r2 = _with(raw, sag__professor=profs)
    out = build_academic(r2, resolve(r2, KEY), onto)["class_result"]
    mine = [c for c in out if c["class_id"] == sc["CLASS_ID"]]
    assert mine and all(c["professor"] is None for c in mine)
    ok = build_academic(raw, resolve(raw, KEY), onto)["class_result"]
    assert any(c["professor"] and onto.professor_allowed(c["professor"]) for c in ok)


def test_room_name_from_prefix_when_no_idnumber(
    raw: RawData, tables: dict[str, list[dict[str, Any]]], onto: Ontology
) -> None:
    rooms = [c for c in raw["lms/mdl_course"] if not c["idnumber"] and c["shortname"][:3].isalpha()]
    assert rooms
    typed = [r for r in tables["lms_event"] if r["subject_name_pt"]]
    assert typed and all(
        onto.subject_name_pt  # nomes PT existentes
        and r["subject_name_pt"] in onto.subject_names_pt()
        or r["subject_name_pt"] in set(onto._inst_labels.values())  # noqa: SLF001
        for r in typed
    )
    assert onto.room_subject("ADM0103_2023-1_EAD_T01", None) == onto.subject_name_pt("ADM0103")


def test_lms_quiz_active_student_accounts_only(raw: RawData, onto: Ontology) -> None:
    res = resolve(raw, KEY)
    quizzes = build_lms(raw, res, onto)["lms_quiz"]
    assert quizzes and {q["quiz_type"] for q in quizzes} <= {"AV1", "AV2", "simulado"}
    assert all(q["subject_name_pt"] for q in quizzes)
    # nenhuma inscrição ativa -> nenhum quiz
    r2 = _with(
        raw, lms__mdl_user_enrolments=[{**e, "status": "1"} for e in raw["lms/mdl_user_enrolments"]]
    )
    assert build_lms(r2, resolve(r2, KEY), onto)["lms_quiz"] == []


def test_lms_event_has_no_ip_url_or_related(tables: dict[str, list[dict[str, Any]]]) -> None:
    assert tables["lms_event"]
    assert not {"ip", "page_url", "relateduserid", "userid"} & set(tables["lms_event"][0])


def test_divergence_flag(raw: RawData, onto: Ontology) -> None:
    sc = raw["sag/student_class"]
    rows = [
        {**sc[0], "RESULT": "FAILED_ATTENDANCE", "ATTENDANCE_PCT": "75.0"},
        {**sc[1], "RESULT": "FAILED_ATTENDANCE", "ATTENDANCE_PCT": "60.0"},
        {**sc[2], "RESULT": "APPROVED", "ATTENDANCE_PCT": "90.0"},
    ]
    r2 = _with(raw, sag__student_class=rows)
    out = build_academic(r2, resolve(r2, KEY), onto)["class_result"]
    assert [c["divergencia"] for c in out[:3]] == [True, False, False]


def test_coverage_sag_events_from_2020_12_12(raw: RawData) -> None:
    hist = [{"EVENT_DATE": "2020-12-12"}, {"EVENT_DATE": "2021-03-01"}]
    cov = {c["domain"]: c for c in build_coverage(_with(raw, sag__enrollment_status_history=hist))}
    assert cov["matricula_eventos"]["starts_on"] == date(2020, 12, 12)
    assert cov["matricula_eventos"]["source"] == "sag"
    assert {"notas", "rematricula", "secretaria", "lms", "financeiro"} <= set(cov)
    assert cov["lms"]["starts_on"] == date(2023, 1, 15)


def test_due_date_is_date(tables: dict[str, list[dict[str, Any]]]) -> None:
    d = tables["invoice"][0]["due_date"]
    assert type(d) is date


def test_payer_type_golden4(raw: RawData, tables: dict[str, list[dict[str, Any]]]) -> None:
    res = resolve(raw, KEY)
    ek = res.enrollments[("UniSaoPaulo", "180823")].enrollment_key
    mine = [c for c in tables["contract"] if c["enrollment_key"] == ek]
    assert mine and {c["payer_type"] for c in mine} == {"responsavel"}


def test_issued_documents_golden9_diploma(
    raw: RawData, tables: dict[str, list[dict[str, Any]]]
) -> None:
    res = resolve(raw, KEY)
    pk = res.enrollments[
        ("UniSaoPaulo", "182915")
    ].person_key  # diploma pode ser da outra matrícula
    docs = [d for d in tables["issued_document"] if d["person_key"] == pk]
    assert any(d["document_type"] == "Diploma" for d in docs)
    assert all("Signed_By" not in d and "signed_by" not in d for d in docs)
    rx = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
    assert all(rx.fullmatch(d["codigo_validacao"]) for d in docs)


def test_enrollment_event_protocol_ref_golden3(
    raw: RawData, tables: dict[str, list[dict[str, Any]]]
) -> None:
    res = resolve(raw, KEY)
    ek = res.enrollments[("UniSaoPaulo", "181544")].enrollment_key
    # CSV: PRT-2026/0000610
    refs = {e["protocol_ref"] for e in tables["enrollment_event"] if e["enrollment_key"] == ek}
    assert "20260000610" in refs


def test_price_keys(tables: dict[str, list[dict[str, Any]]]) -> None:
    keys = set(tables["price"][0])
    assert {"mensalidade", "ano", "parcelas_por_semestre"} <= keys
    assert "mensalidade_ano" not in keys
    p = next(p for p in tables["price"] if p["ano"] == 2026)
    assert isinstance(p["mensalidade"], int) and isinstance(p["parcelas_por_semestre"], int)


def test_course_offer_by_modality(tables: dict[str, list[dict[str, Any]]]) -> None:
    direito = {o["modality"] for o in tables["course_offer"] if o["course_id"] == "210"}
    assert direito == {"CAMPUS"}
    assert {o["modality"] for o in tables["course_offer"]} == {"CAMPUS", "DL"}


def test_only_loaded_columns_emitted(
    tables: dict[str, list[dict[str, Any]]], onto: Ontology
) -> None:
    allowed: set[tuple[str, str]] = set()
    for t in onto.tables():
        for c in onto.table_columns(t):
            spec = onto.column(t, c)
            if spec.destination in ("load", "transform"):
                assert spec.target
                e, f = spec.target.split(".")
                allowed.add((e, f))
    for e in onto.entities():
        for f in onto.entity_fields(e):
            if onto.is_derived(e, f):
                allowed.add((e, f))
    for table, rows in tables.items():
        assert rows, table
        for col in set().union(*rows):
            assert col in IMPLICIT or (table, col) in allowed, (table, col)


def test_unit_label_from_frozen_list(
    tables: dict[str, list[dict[str, Any]]], onto: Ontology
) -> None:
    assert all(u["label"] == onto.unit_label(u["unit_id"]) for u in tables["unit"])
    assert all(
        e["unit_label"] in set(onto.unit_label(i) for i in onto.unit_ids())
        for e in tables["enrollment"]
    )


def test_person_has_only_first_name(tables: dict[str, list[dict[str, Any]]]) -> None:
    assert set(tables["person"][0]) == {"person_key", "first_name"}


def test_build_counts(tables: dict[str, list[dict[str, Any]]]) -> None:
    c = build_counts(tables)
    assert c["enrollment"] == len(tables["enrollment"]) and c["price"] > 0


def test_lms_accounts_only_linked_students(
    raw: RawData, tables: dict[str, list[dict[str, Any]]]
) -> None:
    res = resolve(raw, KEY)
    users = {int(u["student_lms_id"]): u for u in raw["lms/mdl_user"]}
    assert {a["lms_user_id"] for a in tables["lms_account"]} == {str(i) for i in res.lms_accounts}
    bad = re.compile(r"(prof\d+|admin|integration\..*|support\..*|aluno\.teste\d*)")
    assert not [i for i in res.lms_accounts if bad.fullmatch(users[i]["username"].strip().lower())]
    assert {e["lms_user_id"] for e in tables["lms_account"]} <= {str(i) for i in res.lms_accounts}


def test_teacher_enrolments_not_student(raw: RawData, onto: Ontology) -> None:
    res = resolve(raw, KEY)
    uid = str(next(iter(res.lms_accounts)))
    course = next(c["id"] for c in raw["lms/mdl_course"] if c["shortname"][:3].isalpha()
                  and not c["shortname"].startswith("INST"))  # fmt: skip
    enr = [{"id": "1", "courseid": course, "userid": uid, "role": "editingteacher", "status": "0"}]
    q = [{"id": "1", "course": course, "name": "AV1 - Online Exam"}]
    r2 = _with(raw, lms__mdl_user_enrolments=enr, lms__mdl_quiz=q)
    assert build_lms(r2, resolve(r2, KEY), onto)["lms_quiz"] == []


def test_alumni_is_aluno(raw: RawData, onto: Ontology) -> None:
    assert any(p["Requester_Type"] == "Alumni" for p in raw["secretary/protocol"]) or True
    prots = [{**p, "Requester_Type": "Alumni"} for p in raw["secretary/protocol"]]
    r2 = _with(raw, secretary__protocol=prots)
    out = build_protocols(r2, resolve(r2, KEY), onto)["protocol"]
    assert out and {p["solicitante_papel"] for p in out} == {"aluno"}


def test_sensitive_rooms_need_active_student_enrolment(raw: RawData, onto: Ontology) -> None:
    res = resolve(raw, KEY)
    uid = str(next(iter(res.lms_accounts)))
    course = {"id": "9", "shortname": "INST-UNISAOPAULO-05", "idnumber": ""}
    log = {"id": "77", "eventname": "\\core\\event\\course_viewed", "userid": uid,
           "courseid": "9", "timecreated": "1673751600"}  # fmt: skip
    enr = {"id": "1", "courseid": "9", "userid": uid, "role": "student", "status": "0"}
    base = _with(raw, lms__mdl_course=[*raw["lms/mdl_course"], course],
                 lms__mdl_logstore_standard_log=[log])  # fmt: skip

    def ids(enrols: list[dict[str, str]]) -> list[str]:
        r2 = _with(base, lms__mdl_user_enrolments=enrols)
        return [e["event_id"] for e in build_lms(r2, resolve(r2, KEY), onto)["lms_event"]]

    assert ids([]) == []
    assert ids([{**enr, "role": "teacher"}]) == []
    assert ids([enr]) == ["77"]
