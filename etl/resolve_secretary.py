"""Resolução dos protocolos da secretaria: protocolo -> matrícula (marca, RA).

Nunca liga por RA sozinho: o CPF do solicitante tem de ser do aluno (ra_cpf) ou, para o
responsável, de um pagador de contrato daquela matrícula. Sem vínculo quando ambíguo.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from core.normalize import digits, normalize_cpf, normalize_ra
from etl.resolve_core import Enrollment, EnrollmentKey, Payer, Person, Row, resolve_contracts

Rule = Literal[
    "ra_cpf", "guardian_payer", "guardian_payer_unique", "cpf_brand_unique", "cpf_brand_by_date"
]

# shortcut: prefixo de Unit_Code -> família (test_unit_prefix_map_matches_csv); atualizar se mudar.
_UNIT_FAMILY = {
    "SPA": "UniSaoPaulo",
    "MON": "UniMontanhas",
    "LIT": "UniLitoral",
    "ATL": "UniAtlantico",
    "ARA": "UniAraucaria",
}


@dataclass(frozen=True)
class ProtocolLink:
    enrollment: EnrollmentKey
    rule: Rule


def protocol_number(ref: str) -> str:
    """'PRT-2022/0003006' -> '20220003006' (só dígitos)."""
    return digits(ref)


def _brand(unit_code: str) -> str | None:
    family = _UNIT_FAMILY.get(unit_code.strip().upper()[:3])
    return family and (f"{family}_EAD" if unit_code.strip().upper().endswith("-EAD") else family)


def _current(candidates: Sequence[Enrollment], day: date) -> Enrollment:
    """Vigente na data: a 1ª que termina (status_date) em ou depois dela; senão a mais recente.

    status_date vazio = em aberto (ordena por último). Empate de data: a encerrada (não ACTIVE)
    vem antes da ACTIVE (transferência: até o dia vigorava a antiga); depois, menor RA.
    Data não ISO levanta ValueError (nada de adivinhar formato).
    """

    def end(e: Enrollment) -> tuple[date, bool, str]:
        d = date.fromisoformat(e.status_date) if e.status_date else date.max
        return d, e.status == "ACTIVE", e.ra

    ordered = sorted(candidates, key=end)
    return next((e for e in ordered if end(e)[0] >= day), ordered[-1])


def resolve_protocols(
    protocols: Sequence[Row],
    people: Mapping[str, Person],
    enrollments: Mapping[EnrollmentKey, Enrollment],
    contracts: Sequence[Row],
    payers: Mapping[str, Payer],
    *,
    customers: Sequence[Row],
) -> dict[str, ProtocolLink]:
    """Protocolo -> ProtocolLink. Terceiro, ambíguo ou sem CPF conhecido fica de fora."""
    cpf_of_customer = {c["customerId"]: normalize_cpf(c.get("taxId", "")) for c in customers}
    paid_by: dict[str, set[EnrollmentKey]] = defaultdict(set)  # CPF do pagador -> matrículas
    target_of = resolve_contracts(contracts, enrollments)
    for r in contracts:
        cpf = cpf_of_customer.get(r["customerId"])
        target = target_of.get(r["contractId"])
        if cpf and target and payers[r["customerId"]].payer_type != "empresa":
            paid_by[cpf].add(target)
    by_person: dict[UUID, list[Enrollment]] = defaultdict(list)
    for e in enrollments.values():
        by_person[e.person_key].append(e)

    out: dict[str, ProtocolLink] = {}
    for p in protocols:
        brand = _brand(p["Unit_Code"])
        cpf = normalize_cpf(p["Requester_CPF"])
        if brand is None or cpf is None or p["Requester_Type"] == "Third Party":
            continue
        person = people.get(cpf)
        ra = normalize_ra(p["Stu_ID"].strip().zfill(6)) if p["Stu_ID"].strip() else None
        found = enrollments.get((brand, ra)) if ra else None
        link: ProtocolLink | None = None
        if person is not None and found and found.person_key == person.person_key:
            link = ProtocolLink((brand, found.ra), "ra_cpf")
        elif p["Requester_Type"] == "Guardian":
            paid = {k for k in paid_by.get(cpf, ()) if k[0] == brand}
            if found and (brand, found.ra) in paid:
                link = ProtocolLink((brand, found.ra), "guardian_payer")
            elif not p["Stu_ID"].strip() and len(paid) == 1:
                link = ProtocolLink(next(iter(paid)), "guardian_payer_unique")
        elif person is not None:
            mine = [e for e in by_person[person.person_key] if e.brand == brand]
            if len(mine) == 1:
                link = ProtocolLink((brand, mine[0].ra), "cpf_brand_unique")
            elif mine:
                cur = _current(mine, date.fromisoformat(p["Opened_At"][:10]))
                link = ProtocolLink((brand, cur.ra), "cpf_brand_by_date")
        if link:
            out[p["Protocol"]] = link
    return out
