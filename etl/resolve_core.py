"""Resolução de identidade: pessoas (por CPF), matrículas (marca, RA), contratos e pagadores.

Nunca liga por RA sozinho (RAs EAD colidem entre marcas) nem por pagador (customerId/taxId).
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid5

from core.keys import hmac_uuid
from core.normalize import normalize_academic_ref, normalize_cpf, normalize_ra

Row = Mapping[str, str]
EnrollmentKey = tuple[str, str]  # (brand, ra)

# shortcut: mapa estático companyCode -> família (testado contra company.csv); atualizar se mudar.
_COMPANY_FAMILY = {
    "1010": "UniSaoPaulo",
    "1020": "UniMontanhas",
    "1030": "UniLitoral",
    "1040": "UniAtlantico",
    "1050": "UniAraucaria",
}


@dataclass(frozen=True)
class Person:
    person_key: UUID
    first_name: str | None
    person_ids: tuple[str, ...] = ()  # PERSON_ID de origem (vários por CPF)


@dataclass(frozen=True)
class Enrollment:
    enrollment_key: UUID
    person_key: UUID
    brand: str
    ra: str
    status: str
    course: str = ""
    curriculum_id: str = ""
    unit_id: str = ""
    modality: str = ""
    shift: str = ""
    semester: str = ""
    status_date: str = ""
    origin_brand: str = ""
    origin_ra: str = ""


@dataclass(frozen=True)
class Payer:
    person_key: UUID | None
    payer_type: Literal["aluno", "responsavel", "empresa"]


@dataclass
class ResolutionStats:
    unresolved: int = 0
    duplicates: int = 0  # (marca, RA) repetido por CPFs diferentes: fica o primeiro


def first_name(full_name: str) -> str | None:
    parts = full_name.split()
    return parts[0].capitalize() if parts else None


def resolve_people(
    rows: Sequence[Row], hmac_key: bytes, stats: ResolutionStats | None = None
) -> dict[str, Person]:
    """Uma Person por CPF normalizado; PERSON_IDs com o mesmo CPF colapsam."""
    ids: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for r in rows:
        cpf = normalize_cpf(r.get("CPF", ""))
        if cpf is None:
            if stats:
                stats.unresolved += 1
            continue
        ids.setdefault(cpf, []).append(r["PERSON_ID"])
        names.setdefault(cpf, r.get("FULL_NAME", ""))
    return {
        cpf: Person(hmac_uuid(hmac_key, cpf), first_name(names[cpf]), tuple(pids))
        for cpf, pids in ids.items()
    }


def resolve_enrollments(
    rows: Sequence[Row], people: Mapping[str, Person], stats: ResolutionStats | None = None
) -> dict[EnrollmentKey, Enrollment]:
    by_person_id = {pid: p for p in people.values() for pid in p.person_ids}
    out: dict[EnrollmentKey, Enrollment] = {}
    for r in rows:
        ra = normalize_ra(r["STUDENT_ID"])
        person = by_person_id.get(r["PERSON_ID"])
        if ra is None or person is None:
            if stats:
                stats.unresolved += 1
            continue
        brand = r["BRAND"]
        if (brand, ra) in out:
            if stats:
                stats.duplicates += 1
            continue
        out[(brand, ra)] = Enrollment(
            # derivada da person_key: determinística e não reversível sem o segredo
            enrollment_key=uuid5(person.person_key, f"{brand}:{ra}"),
            person_key=person.person_key,
            brand=brand,
            ra=ra,
            status=r["ENROLLMENT_STATUS"],
            course=r.get("COURSE", ""),
            curriculum_id=r.get("CURRICULUM_ID", ""),
            unit_id=r.get("UNIT_ID", ""),
            modality=r.get("MODALITY", ""),
            shift=r.get("SHIFT", ""),
            semester=r.get("SEMESTER", ""),
            status_date=r.get("STATUS_DATE", ""),
            origin_brand=r.get("ORIGIN_BRAND", ""),
            origin_ra=r.get("ORIGIN_STUDENT_ID", ""),
        )
    return out


def brand_for_company(company_code: str, business_area: str) -> str:
    """companyCode + businessArea -> BRAND do SAG (DIGITAL = EAD). KeyError se desconhecido."""
    family = _COMPANY_FAMILY[company_code.strip()]
    return f"{family}_EAD" if business_area.strip().upper() == "DIGITAL" else family


def _contract_target(r: Row) -> EnrollmentKey | None:
    ref = normalize_academic_ref("".join(r["academicRef"].split()))
    try:
        brand = brand_for_company(r["companyCode"], r["businessArea"])
    except KeyError:
        return None
    return None if ref is None else (brand, ref)


def resolve_contracts(
    rows: Sequence[Row],
    enrollments: Mapping[EnrollmentKey, Enrollment],
    stats: ResolutionStats | None = None,
) -> dict[str, EnrollmentKey]:
    """contractId -> (brand, ra), só por (marca, academicRef); o pagador não entra."""
    out: dict[str, EnrollmentKey] = {}
    for r in rows:
        target = _contract_target(r)
        if target is None or target not in enrollments:
            if stats:
                stats.unresolved += 1
            continue
        out[r["contractId"]] = target
    return out


def resolve_payers(
    customers: Sequence[Row],
    contracts: Sequence[Row],
    people: Mapping[str, Person],
    enrollments: Mapping[EnrollmentKey, Enrollment],
) -> dict[str, Payer]:
    """customerId -> Payer. empresa = COMPANY; aluno = paga a própria matrícula; senão responsável.

    person_key é None se o pagador não é pessoa conhecida (empresa, responsável sem matrícula).
    """
    own: set[str] = set()  # customerIds que pagam a matrícula de si mesmos
    cpf_of = {c["customerId"]: normalize_cpf(c.get("taxId", "")) for c in customers}
    for r in contracts:
        target = _contract_target(r)
        cpf = cpf_of.get(r["customerId"])
        person = people.get(cpf) if cpf else None
        enrollment = enrollments.get(target) if target else None
        if enrollment and person and enrollment.person_key == person.person_key:
            own.add(r["customerId"])
    out: dict[str, Payer] = {}
    for c in customers:
        cid = c["customerId"]
        if c["customerType"] == "COMPANY":
            out[cid] = Payer(None, "empresa")
            continue
        cpf = cpf_of[cid]
        person = people.get(cpf) if cpf else None
        out[cid] = Payer(
            person.person_key if person else None, "aluno" if cid in own else "responsavel"
        )
    return out
