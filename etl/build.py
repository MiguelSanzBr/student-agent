# ruff: noqa: B023  (lambdas de `_typed` são chamadas na hora, dentro da iteração)
"""Transformações: CSV cru + resoluções + ontologia -> linhas canônicas tipadas.

Só colunas com destino load/transform saem (o resto nunca é lido). Texto livre vira tipo:
mensagens viram `modelo` (rótulo PT) ou `texto_livre` (nunca vai ao LLM); professor, unidade,
curso e disciplina só saem como rótulo/lista exata da ontologia.
"""

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from core.keys import brand_family
from core.normalize import (
    SP,
    normalize_ra,
    parse_bool,
    parse_brl,
    parse_epoch,
    parse_naive_local,
)
from core.ontology import Ontology
from etl.raw import RawData
from etl.resolve_core import (
    Enrollment,
    Payer,
    Person,
    brand_for_company,
    resolve_contracts,
    resolve_enrollments,
    resolve_payers,
    resolve_people,
)
from etl.resolve_lms import resolve_lms_accounts
from etl.resolve_secretary import ProtocolLink, protocol_number, resolve_protocols

Row = dict[str, Any]
Counts = dict[str, int]
ONTOLOGY_ROOT = Path(__file__).resolve().parents[1] / "ontology"
_ROLE = {
    "Student": "aluno",
    "Alumni": "aluno",
    "Guardian": "responsavel",
    "Third Party": "terceiro",
}
_SENSITIVE_ROOMS = ("-05", "-06")  # INST-*-05 (professores) e -06 (testes): só com inscrição ativa


@dataclass(frozen=True)
class Resolution:
    people: Mapping[str, Person]  # chave = CPF cru normalizado: nunca persistir nem logar
    enrollments: Mapping[tuple[str, str], Enrollment]
    contracts: Mapping[str, tuple[str, str]]
    payers: Mapping[str, Payer]
    links: Mapping[str, ProtocolLink]
    lms_accounts: Mapping[int, tuple[str, str]]


def resolve(raw: RawData, hmac_key: bytes) -> Resolution:
    people = resolve_people(raw["sag/person"], hmac_key)
    enr = resolve_enrollments(raw["sag/enrollment"], people)
    customers, contracts = raw["finance/customer"], raw["finance/contract"]
    payers = resolve_payers(customers, contracts, people, enr)
    return Resolution(
        people,
        enr,
        resolve_contracts(contracts, enr),
        payers,
        resolve_protocols(
            raw["secretary/protocol"], people, enr, contracts, payers, customers=customers
        ),
        resolve_lms_accounts(raw["lms/mdl_user"], raw["lms/mdl_logstore_standard_log"], enr),
    )


def load_ontology(raw: RawData, root: Path = ONTOLOGY_ROOT) -> Ontology:
    return Ontology.load(root, {r["CLASS_ID"]: r["SUBJECT_CODE"] for r in raw["sag/class_section"]})


# ----------------------------------------------------------------------------- tipagem genérica
def _instant(v: str) -> datetime | None:
    if v.isdigit():
        return parse_epoch(v)
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return parse_naive_local(v) if dt.tzinfo is None else dt.astimezone(UTC)


def _conv(v: str, typ: str) -> Any:
    v = v.strip()
    if not v:
        return None
    if typ == "date":
        return date.fromisoformat(v)
    if typ == "instant":
        return _instant(v)
    if typ == "money":
        return parse_brl(v) if "R$" in v else int(v)
    if typ == "number":
        return int(v) if re.fullmatch(r"-?\d+", v) else Decimal(v)
    if typ == "bool":
        return parse_bool(v)
    return v


Custom = Mapping[str, Callable[[Mapping[str, str]], Any]]


def _typed(onto: Ontology, table: str, r: Mapping[str, str], custom: Custom = {}) -> Row:  # noqa: B006
    out: Row = {}
    for col in onto.table_columns(table):
        spec = onto.column(table, col)
        if spec.target is None:  # só load/transform têm alvo
            continue
        field = spec.target.split(".")[1]
        out[field] = custom[col](r) if col in custom else _conv(r[col], spec.type)
    return out


def _owner(res: Resolution, brand: str, raw_ra: str) -> Row | None:
    ra = normalize_ra(raw_ra)
    e = res.enrollments.get((brand, ra)) if ra else None
    return None if e is None else {"person_key": e.person_key, "enrollment_key": e.enrollment_key}


def _owned(
    raw: RawData,
    res: Resolution,
    onto: Ontology,
    table: str,
    custom: Custom = {},  # noqa: B006
    owner: Callable[[Mapping[str, str]], Row | None] | None = None,
) -> list[Row]:
    own = owner or (lambda r: _owner(res, r["BRAND"], r["STUDENT_ID"]))
    out = []
    for r in raw[table]:
        k = own(r)
        if k is not None:
            out.append({**k, **_typed(onto, table, r, custom)})
    return out


# ----------------------------------------------------------------------------- acadêmico
def build_academic(raw: RawData, res: Resolution, onto: Ontology) -> dict[str, list[Row]]:
    sections = {s["CLASS_ID"]: s for s in raw["sag/class_section"]}
    prof_name = {p["PROFESSOR_ID"]: p["FULL_NAME"].strip() for p in raw["sag/professor"]}

    def derived(r: Mapping[str, str]) -> Row:
        s = sections.get(r["CLASS_ID"])
        name = prof_name.get(s["PROFESSOR_ID"]) if s else None
        code = s["SUBJECT_CODE"] if s else None
        att = r["ATTENDANCE_PCT"].strip()
        return {
            "subject_code": code,
            "subject_name_pt": onto.subject_name_pt(code) if code else None,
            "term": s["TERM"] if s else None,
            "section": s["SECTION"] if s else None,
            "professor": name if name and onto.professor_allowed(name) else None,
            "divergencia": r["RESULT"] == "FAILED_ATTENDANCE" and bool(att) and Decimal(att) >= 75,
        }

    classes = []
    for r in raw["sag/student_class"]:
        k = _owner(res, r["BRAND"], r["STUDENT_ID"])
        if k is not None:
            classes.append({**k, **_typed(onto, "sag/student_class", r, {}), **derived(r)})
    enrollment = _owned(
        raw,
        res,
        onto,
        "sag/enrollment",
        {"UNIT_ID": lambda r: onto.unit_label(r["UNIT_ID"]) if r["UNIT_ID"].strip() else None},
    )
    for row, e in zip(enrollment, _enrollments_in_order(raw, res), strict=True):
        row["ra"] = e.ra
    return {
        "person": [
            {"person_key": p.person_key, "first_name": p.first_name} for p in res.people.values()
        ],  # fmt: skip
        "enrollment": enrollment,
        "enrollment_event": _owned(
            raw,
            res,
            onto,
            "sag/enrollment_status_history",
            {"PROTOCOL_REF": lambda r: protocol_number(r["PROTOCOL_REF"]) or None},
        ),
        "term_enrollment": _owned(raw, res, onto, "sag/term_enrollment"),
        "class_result": classes,
    }


def _enrollments_in_order(raw: RawData, res: Resolution) -> list[Enrollment]:
    """Matrículas resolvidas na ordem das linhas do CSV (mesma regra de `_owned`)."""
    out = []
    for r in raw["sag/enrollment"]:
        ra = normalize_ra(r["STUDENT_ID"])
        e = res.enrollments.get((r["BRAND"], ra)) if ra else None
        if (
            e is not None
            and e.status == r["ENROLLMENT_STATUS"]
            and e.status_date == r["STATUS_DATE"]
        ):
            out.append(e)
    return out


# ----------------------------------------------------------------------------- financeiro
def build_finance(raw: RawData, res: Resolution, onto: Ontology) -> dict[str, list[Row]]:
    def of_contract(r: Mapping[str, str]) -> Row | None:
        t = res.contracts.get(r["contractId"])
        return _owner(res, *t) if t else None

    ref = {"protocolRef": lambda r: protocol_number(r["protocolRef"]) or None}
    out: dict[str, list[Row]] = {}
    contracts = []
    cust = {c["contractId"]: c["customerId"] for c in raw["finance/contract"]}
    for r in raw["finance/contract"]:
        k = of_contract(r)
        if k is not None:
            payer = res.payers[cust[r["contractId"]]].payer_type
            contracts.append({**k, **_typed(onto, "finance/contract", r), "payer_type": payer})
    out["contract"] = contracts
    out["invoice"] = _owned(raw, res, onto, "finance/invoice", ref, of_contract)
    inv_owner = {i["invoice_id"]: {k: i[k] for k in ("person_key", "enrollment_key")}
                 for i in out["invoice"]}  # fmt: skip
    by_invoice = lambda r: inv_owner.get(r["invoiceId"])  # noqa: E731
    out["payment"] = _owned(raw, res, onto, "finance/payment", owner=by_invoice)
    out["late_charge"] = _owned(raw, res, onto, "finance/late_charge", owner=by_invoice)
    out["discount"] = _owned(raw, res, onto, "finance/discount", owner=of_contract)
    out["agreement"] = _owned(raw, res, onto, "finance/agreement", ref, of_contract)
    ag_owner = {a["agreement_id"]: {k: a[k] for k in ("person_key", "enrollment_key")}
                for a in out["agreement"]}  # fmt: skip
    out["agreement_invoice"] = _owned(
        raw, res, onto, "finance/agreement_invoice", owner=lambda r: ag_owner.get(r["agreementId"])
    )
    return out


# ----------------------------------------------------------------------------- protocolos
def build_protocols(raw: RawData, res: Resolution, onto: Ontology) -> dict[str, list[Row]]:
    def of_protocol(r: Mapping[str, str]) -> Row | None:
        link = res.links.get(r["Protocol"])
        return _owner(res, *link.enrollment) if link else None

    papel = {p["Protocol"]: _ROLE[p["Requester_Type"]] for p in raw["secretary/protocol"]}
    protocols = []
    for r in raw["secretary/protocol"]:
        k = of_protocol(r)
        if k is not None:
            protocols.append(
                {
                    **k,
                    **_typed(onto, "secretary/protocol", r),
                    "solicitante_papel": papel[r["Protocol"]],
                }
            )

    def message(r: Mapping[str, str]) -> Row | None:
        if r["Author_Type"] not in ("Analyst", "System") or parse_bool(r["Is_Internal_Note"]):
            return None
        k = of_protocol(r)
        if k is None:
            return None
        modelo = onto.classify_message(r["Message"])
        return {
            **k,
            **_typed(onto, "secretary/protocol_interaction", r, {"Message": lambda _r: modelo}),
            "texto_livre": None if modelo else r["Message"],
        }

    messages = [m for r in raw["secretary/protocol_interaction"] if (m := message(r))]
    docs = _owned(raw, res, onto, "secretary/issued_document", owner=of_protocol)
    rx = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}")
    for d in docs:
        if d["codigo_validacao"] and not rx.fullmatch(d["codigo_validacao"]):
            d["codigo_validacao"] = None
    services = []
    for r in raw["secretary/demand_type"]:
        s = onto.service(r["Demand_Code"])
        services.append(
            {
                **_typed(onto, "secretary/demand_type", r, {"Demand_Name": lambda _r: s.name_pt}),
                "ui_label": s.ui_label,
                "mode": s.mode,
            }
        )
    return {
        "protocol": protocols,
        "protocol_event": _owned(
            raw, res, onto, "secretary/protocol_status_history", owner=of_protocol
        ),
        "protocol_message": messages,
        "issued_document": docs,
        "service": services,
    }


# ----------------------------------------------------------------------------- LMS
def build_lms(raw: RawData, res: Resolution, onto: Ontology) -> dict[str, list[Row]]:
    accounts = res.lms_accounts  # só contas de aluno ligadas à matrícula (Task 9)

    def of_user(uid: str) -> Row | None:
        t = accounts.get(int(uid))
        return _owner(res, *t) if t else None

    courses = {c["id"]: c for c in raw["lms/mdl_course"]}
    active = {  # (userid, courseid) com inscrição ativa de aluno; professores ficam de fora
        (e["userid"], e["courseid"])
        for e in raw["lms/mdl_user_enrolments"]
        if e["role"] == "student" and e["status"] == "0"
    }

    def room(course_id: str) -> str | None:
        c = courses.get(course_id)
        return onto.room_subject(c["shortname"], c["idnumber"] or None) if c else None

    def sensitive(course_id: str) -> bool:
        c = courses.get(course_id)
        return (
            c is not None
            and c["shortname"].startswith("INST-")
            and c["shortname"].endswith(_SENSITIVE_ROOMS)
        )

    users = []
    for r in raw["lms/mdl_user"]:
        k = of_user(r["student_lms_id"])
        if k is not None:
            users.append({**k, **_typed(onto, "lms/mdl_user", r)})
    events = []
    for r in raw["lms/mdl_logstore_standard_log"]:
        k = of_user(r["userid"])
        if k is None or (sensitive(r["courseid"]) and (r["userid"], r["courseid"]) not in active):
            continue
        events.append({**k, **_typed(onto, "lms/mdl_logstore_standard_log", r),
                       "subject_name_pt": room(r["courseid"])})  # fmt: skip
    quizzes_by_course: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for q in raw["lms/mdl_quiz"]:
        quizzes_by_course[q["course"]].append(q)
    quizzes = []
    for uid, course in sorted(active):
        k = of_user(uid)
        subject = room(course)
        if k is None or subject is None:
            continue
        for q in quizzes_by_course.get(course, ()):
            kind = onto.quiz_type(q["name"])
            if kind is not None:
                quizzes.append({**k, **_typed(onto, "lms/mdl_quiz", q, {"name": lambda _r: kind}),
                                "subject_name_pt": subject})  # fmt: skip
    attempts = []
    for a in raw["lms/mdl_quiz_attempts"]:
        k = of_user(a["userid"])
        if k is not None:
            attempts.append({**k, **_typed(onto, "lms/mdl_quiz_attempts", a)})
    return {
        "lms_account": users,
        "lms_event": events,
        "lms_quiz": quizzes,
        "lms_quiz_attempt": attempts,
    }


# ----------------------------------------------------------------------------- catálogo
def build_catalog(raw: RawData, onto: Ontology) -> dict[str, list[Row]]:
    def plain(table: str, custom: Custom = {}) -> list[Row]:  # noqa: B006
        return [_typed(onto, table, r, custom) for r in raw[table]]

    unit_brand = {u["UNIT_ID"]: u["BRAND"] for u in raw["sag/unit"]}
    brand_modality = {b["BRAND"]: b["MODALITY"] for b in raw["sag/brand"]}
    brands = plain("sag/brand")
    for br in brands:
        br["family"] = brand_family(br["brand"])
    units = plain("sag/unit", {"UNIT_NAME": lambda r: onto.unit_label(r["UNIT_ID"])})
    for u in units:
        u["brand_family"] = brand_family(u["brand"])
    offers = plain("sag/course_offer")
    for off in offers:
        b = unit_brand[off["unit_id"]]
        off["modality"], off["brand_family"] = brand_modality[b], brand_family(b)
    acr = {c["COURSE_ACRONYM"]: c["COURSE"] for c in raw["sag/course"]}
    installments = {
        p["productCode"]: int(p["installmentsPerTerm"])
        for p in raw["finance/product"]
        if p["installmentsPerTerm"]
    }
    prices = []
    for r in raw["finance/price_list"]:
        code = r["productCode"]
        if not code.startswith("GRAD-"):  # taxas de serviço já estão em `service`
            continue
        _, acronym, kind = code.split("-")
        prices.append(
            {
                **_typed(
                    onto,
                    "finance/price_list",
                    r,
                    {
                        "productCode": lambda _r: acr[acronym],
                        "companyCode": lambda r: brand_family(
                            brand_for_company(r["companyCode"], "")
                        ),
                        "validFrom": lambda r: int(r["validFrom"][:4]),
                    },
                ),
                "modality": "DL" if kind == "DIG" else "CAMPUS",
                "parcelas_por_semestre": installments[code],
            }
        )
    return {
        "brand": brands,
        "unit": units,
        "course": plain("sag/course", {"COURSE_NAME": lambda r: onto.course_name_pt(r["COURSE"])}),
        "curriculum": plain("sag/curriculum"),
        "subject": plain(
            "sag/subject", {"SUBJECT_NAME": lambda r: onto.subject_name_pt(r["SUBJECT_CODE"])}
        ),
        "curriculum_subject": plain("sag/curriculum_subject"),
        "term": plain("sag/term"),
        "course_offer": offers,
        "price": prices,
    }


def build_coverage(raw: RawData) -> list[Row]:
    """Início de cada fonte, derivado dos próprios dados (sem fim: dados até a carga)."""
    first_course = min(
        parse_epoch(c["startdate"]) or datetime.max.replace(tzinfo=UTC)
        for c in raw["lms/mdl_course"]
    )

    def first(table: str, col: str, n: int = 10) -> date:
        return min(date.fromisoformat(r[col][:n]) for r in raw[table])

    competence = min(i["competence"] for i in raw["finance/invoice"])
    term_start = first("sag/term", "START_DATE")
    starts = [
        ("sag", "matricula_eventos", first("sag/enrollment_status_history", "EVENT_DATE")),
        ("sag", "notas", term_start),
        ("sag", "rematricula", term_start),
        ("secretary", "secretaria", first("secretary/protocol", "Opened_At").replace(day=1)),
        ("lms", "lms", first_course.astimezone(SP).date()),
        ("finance", "financeiro", date.fromisoformat(competence + "-01")),
    ]
    return [{"source": s, "domain": d, "starts_on": v, "ends_on": None} for s, d, v in starts]


def build_counts(tables: Mapping[str, Sequence[Row]]) -> Counts:
    return {t: len(rows) for t, rows in tables.items()}


def build_all(
    raw: RawData, hmac_key: bytes, root: Path = ONTOLOGY_ROOT, res: Resolution | None = None
) -> dict[str, list[Row]]:
    res, onto = res or resolve(raw, hmac_key), load_ontology(raw, root)
    out: dict[str, list[Row]] = {}
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
