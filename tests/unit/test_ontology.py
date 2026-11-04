import unicodedata
from collections import Counter
from pathlib import Path

import pytest

from app.tools.registry import TOOL_NAMES
from core.ontology import Ontology
from etl.raw import read_raw

ROOT = Path(__file__).resolve().parents[2]
ONTO = Ontology.load(ROOT / "ontology")
RAW = read_raw(ROOT / "tests" / "fixtures" / "mini")
EXPOSED = ("load", "transform")


def _columns() -> list[tuple[str, str]]:
    return [(t, c) for t in ONTO.tables() for c in ONTO.table_columns(t)]


def test_47_tables_destination_counts() -> None:
    tables = ONTO.tables()
    assert len(tables) == 47
    assert Counter(ONTO.table_destination(t) for t in tables) == {
        "loaded": 28,
        "partial": 3,
        "resolution_only": 10,
        "discarded": 6,
    }
    assert all(len(ONTO.table_reason(t)) > 10 for t in tables)


def test_401_columns_match_headers() -> None:
    headers = {(t, c) for t, rows in RAW.items() for c in rows[0]}
    assert len(headers) == 401
    assert set(_columns()) == headers
    for t, c in headers:
        spec = ONTO.column(t, c)
        assert spec.destination and spec.type and len(spec.reason) >= 3, (t, c)


def test_table_destination_consistent_with_columns() -> None:
    for t in ONTO.tables():
        dests = {ONTO.column(t, c).destination for c in ONTO.table_columns(t)}
        kind = ONTO.table_destination(t)
        if kind == "discarded":
            assert dests == {"discard"}, t
        elif kind == "resolution_only":
            assert dests <= {"resolution", "discard"} and "resolution" in dests, t
        elif kind == "loaded":
            assert dests & set(EXPOSED), t
        else:
            assert dests & set(EXPOSED) and dests - set(EXPOSED), t


def test_customer_resolution_only() -> None:
    assert ONTO.table_destination("finance/customer") == "resolution_only"
    for c in ONTO.table_columns("finance/customer"):
        assert ONTO.column("finance/customer", c).destination in ("resolution", "discard")


# Colunas que nunca podem chegar ao canon (spec 5.4); o teste trava regressões da ontologia.
NEVER_EXPOSED = (
    [
        ("sag/person", c)
        for c in (
            "CPF",
            "BIRTH_DATE",
            "GENDER",
            "MOTHER_NAME",
            "EMAIL",
            "PHONE",
            "ZIP_CODE",
            "CITY",
        )
    ]
    + [("sag/professor", c) for c in ("CPF", "EMAIL", "HIRED_ON", "ACADEMIC_TITLE")]
    + [
        ("sag/brand", "CNPJ"),
        ("sag/enrollment_status_history", "CHANGED_BY"),
        ("secretary/protocol", "Requester_CPF"),
        ("secretary/protocol", "Requester_Name"),
        ("secretary/protocol", "Demand_Analyst"),
        ("secretary/protocol", "Priority"),
        ("secretary/protocol", "SLA_Breached"),
        ("secretary/protocol_status_history", "Changed_By"),
        ("secretary/protocol_interaction", "Author"),
        ("secretary/protocol_interaction", "Is_Internal_Note"),
        ("secretary/issued_document", "Signed_By"),
        ("finance/payment", "transactionRef"),
        ("finance/payment", "bankCode"),
        ("finance/company", "cnpj"),
        ("finance/company", "legalName"),
        ("finance/receivable_snapshot", "collectionStage"),
        ("lms/mdl_logstore_standard_log", "ip"),
        ("lms/mdl_logstore_standard_log", "page_url"),
        ("lms/mdl_logstore_standard_log", "relateduserid"),
        ("lms/mdl_user", "username"),
        ("lms/mdl_user", "email"),
        ("lms/mdl_user", "firstname"),
        ("lms/mdl_user", "lastname"),
    ]
)


@pytest.mark.parametrize(("table", "col"), NEVER_EXPOSED)
def test_sensitive_columns_never_exposed(table: str, col: str) -> None:
    assert ONTO.column(table, col).destination in ("resolution", "discard")


def test_sensitive_tables_discarded() -> None:
    for t in ("finance/invoice_tax", "secretary/satisfaction_survey", "secretary/analyst"):
        assert ONTO.table_destination(t) == "discarded"


def test_targets_are_entity_fields_or_derived() -> None:
    """C19: toda coluna exposta aponta para um campo do canon; campos derivados são declarados."""
    targets = set()
    for t, c in _columns():
        spec = ONTO.column(t, c)
        if spec.destination in EXPOSED:
            assert spec.target is not None, (t, c)
            entity, field = spec.target.split(".")
            assert ONTO.has_field(entity, field), (t, c, spec.target)
            assert not ONTO.is_derived(entity, field), (t, c, spec.target)
            targets.add(spec.target)
        else:
            assert spec.target is None, (t, c)
    for entity in ONTO.entities():
        for field in ONTO.entity_fields(entity):
            assert f"{entity}.{field}" in targets or ONTO.is_derived(entity, field), (
                entity,
                field,
            )
    for d in ("class_result.divergencia", "contract.payer_type", "protocol.solicitante_papel"):
        assert ONTO.is_derived(*d.split("."))
    assert ONTO.is_derived("protocol_message", "texto_livre")


def test_every_exposed_text_field_typed() -> None:
    for t, c in _columns():
        spec = ONTO.column(t, c)
        if spec.destination in EXPOSED and spec.type == "text":
            assert spec.target is not None
            assert ONTO.text_field_type(*spec.target.split(".")) in (
                "enum",
                "label_pt",
                "exact_list",
                "regex",
            ), (t, c)
    free = [k for k, v in ONTO.text_fields().items() if v == "free_text"]
    assert free == ["protocol_message.texto_livre"]
    # campo de texto do canon sem tipo seria exposto sem controle
    for entity in ONTO.entities():
        for field in ONTO.entity_fields(entity):
            if ONTO.field_type(entity, field) == "text":
                assert f"{entity}.{field}" in ONTO.text_fields(), (entity, field)


def test_enums_have_labels() -> None:
    for entity in ONTO.entities():
        for field in ONTO.entity_fields(entity):
            enum = ONTO.field_enum(entity, field)
            if enum:
                assert ONTO.enum_values(enum), enum
    assert ONTO.label_pt("enrollment_status", "ACTIVE") == "Ativa"
    with pytest.raises(KeyError):
        ONTO.label_pt("enrollment_status", "NOPE")


def test_frozen_lists() -> None:
    names = [r["FULL_NAME"] for r in RAW["sag/professor"]]
    assert len(ONTO.professor_names()) == 60
    assert len(ONTO.unit_ids()) == 27
    assert ONTO.professor_allowed(ONTO.professor_names()[0])
    assert not ONTO.professor_allowed("JOAO DA SILVA")
    assert not ONTO.professor_allowed(ONTO.professor_names()[0] + " ")
    assert set(names) <= set(ONTO.professor_names())
    assert ONTO.unit_label("101") == "Campus Paulista"
    with pytest.raises(KeyError):
        ONTO.unit_label("999")


def test_classify_message_exact_after_nfkc() -> None:
    m = "Request received and under analysis."
    label = ONTO.classify_message(m)
    assert label == "Solicitação recebida e em análise."
    assert ONTO.classify_message(m.replace(" ", " ")) == label
    assert ONTO.classify_message(f"  {m}  ") == label
    assert ONTO.classify_message(m.replace("a", "а", 1)) is None  # "а" cirílico
    assert ONTO.classify_message(m + " RA: 012815") is None
    assert ONTO.classify_message("I need an enrollment certificate for my internship.") is None
    assert len({ONTO.classify_message(t) for t in ONTO.message_texts()}) == 4


def test_room_subject_by_idnumber_and_prefix() -> None:
    onto = Ontology.load(ROOT / "ontology", class_subjects={"123": "ADM0103"})
    name = onto.subject_name_pt("ADM0103")
    assert onto.room_subject("GEN0001_2023-1_BAT_T01", "SAG-123") == name  # idnumber vence
    assert onto.room_subject("ADM0103_2023-1_SAV_T01", None) == name
    assert onto.room_subject("ADM0103_2023-1_SAV_T01", "") == name
    assert onto.room_subject("INST-UNISAOPAULO-01", None) == "Orientação ao ensino a distância"
    assert onto.room_subject("INST-UNILITORAL-08", "") == "Semana de boas-vindas"
    assert onto.room_subject("ZZZ9999_2023-1_SAV_T01", None) is None
    assert onto.room_subject("lixo", None) is None


def test_quiz_type() -> None:
    assert ONTO.quiz_type("AV1 - Online Exam") == "AV1"
    assert ONTO.quiz_type("AV2 - Online Exam") == "AV2"
    assert ONTO.quiz_type("Practice Test") == "simulado"
    assert ONTO.quiz_type("AV3 - Online Exam") is None
    assert ONTO.quiz_type("Practice Test 2") is None


def test_services_18_with_name_and_ui_label() -> None:
    codes = ONTO.service_codes()
    assert len(codes) == 18
    assert all(ONTO.service(c).name_pt for c in codes)
    assert ONTO.service("GEN-090").ui_label == "Falar com a secretaria"
    assert ONTO.service("FIN-032").name_pt == "Solicitação de bolsa ou desconto"
    assert ONTO.service("FIN-031").name_pt == "Renegociação de dívida"
    orientation = {c for c in codes if ONTO.service(c).mode == "orientation"}
    assert orientation == {"ACA-010", "ACA-012", "ACA-013", "ACA-014"}
    # C11: prazo, taxa, canais e o porquê de o agente não executar
    demand = {r["Demand_Code"]: r for r in RAW["secretary/demand_type"]}
    for c in codes:
        s = ONTO.service(c)
        assert s.sla_hours == int(demand[c]["SLA_Hours"])
        assert s.fee_cents == int(demand[c]["Service_Fee"].replace("R$ ", "").replace(",", ""))
        assert s.channels and set(s.channels) <= {
            "Portal",
            "WhatsApp",
            "E-mail",
            "Telefone",
            "Presencial",
        }
        assert (s.why_not_agent is not None) == (s.mode == "orientation"), c
    for c in orientation:
        assert len(ONTO.service(c).why_not_agent or "") > 20


def test_courses_and_subjects_named() -> None:
    assert len(RAW["sag/course"]) == 8
    for r in RAW["sag/course"]:
        assert ONTO.course_name_pt(r["COURSE"])
    assert ONTO.course_name_pt("210") == "Direito"
    names = ONTO.subject_names_pt()
    assert len(names) == 260 and len(set(names)) == 260
    assert all(n and unicodedata.normalize("NFC", n) == n for n in names)
    assert ONTO.subject_name_pt("ADM0103") == "Introdução à Administração"


def test_guard_lexicon_sections() -> None:
    lex = ONTO.guard_lexicon()
    for section in (
        lex.entity_nouns,
        lex.debt_nouns,
        lex.negators,
        lex.zero_words,
        lex.commitment_terms,
        lex.action_lemmas,
        lex.confirmation_words,
    ):
        assert section
    assert {"dívida", "saldo", "débito"} <= set(lex.debt_nouns)
    assert {"registrar", "gerar"} <= set(lex.action_lemmas)


def test_tool_yaml_agrees_with_registry() -> None:
    assert set(ONTO.tools()) == set(TOOL_NAMES)
    assert ONTO.tool_outputs("get_catalog") == frozenset()  # T16-19 preenchem
    with pytest.raises(KeyError):
        ONTO.tool_outputs("nope")
