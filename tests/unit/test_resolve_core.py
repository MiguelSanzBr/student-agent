import csv
import io
import zipfile
from pathlib import Path

import pytest

from core.normalize import ra_check_digit
from etl.resolve_core import (
    ResolutionStats,
    brand_for_company,
    resolve_contracts,
    resolve_enrollments,
    resolve_payers,
    resolve_people,
)

KEY = b"k" * 32


def ra(base: str) -> str:
    return base + ra_check_digit(base)


def person(pid: str, cpf: str, name: str = "FULANO DA SILVA") -> dict[str, str]:
    return {"PERSON_ID": pid, "CPF": cpf, "FULL_NAME": name}


def enr(brand: str, student_id: str, pid: str) -> dict[str, str]:
    return {
        "BRAND": brand,
        "STUDENT_ID": student_id,
        "PERSON_ID": pid,
        "ENROLLMENT_STATUS": "ACTIVE",
    }


def contract(cid: str, customer: str, code: str, area: str, ref: str) -> dict[str, str]:
    return {
        "contractId": cid,
        "customerId": customer,
        "companyCode": code,
        "businessArea": area,
        "academicRef": ref,
    }


def test_same_cpf_one_person() -> None:
    people = resolve_people([person("1", "102.854.950-46"), person("2", "10285495046")], KEY)
    assert len(people) == 1
    assert people["10285495046"].person_ids == ("1", "2")


def test_colliding_ra_distinct() -> None:
    brands = ["UniSaoPaulo_EAD", "UniMontanhas_EAD", "UniLitoral_EAD"]
    people = resolve_people([person(str(i), f"{i}0000000001"[:11]) for i in range(3)], KEY)
    rows = [enr(b, "012815", str(i)) for i, b in enumerate(brands)]
    assert ra("01281") == "012815"
    out = resolve_enrollments(rows, people)
    assert len(out) == 3
    assert len({e.person_key for e in out.values()}) == 3
    assert len({e.enrollment_key for e in out.values()}) == 3


def test_academic_ref_variants() -> None:
    people = resolve_people([person("1", "11111111111")], KEY)
    enrollments = resolve_enrollments(
        [
            enr("UniSaoPaulo", ra("01253"), "1"),
            enr("UniSaoPaulo", ra("18015"), "1"),
            enr("UniSaoPaulo", ra("18001"), "1"),
        ],
        people,
    )
    rows = [
        contract("a", "C1", "1010", "PRESENCIAL", "1253-0"),
        contract("b", "C1", "1010", "PRESENCIAL", "18015"),
        contract("c", "C1", "1010", "PRESENCIAL", " 18001-7 "),
    ]
    assert ra("01253") == "012530" and ra("18015") == "180157" and ra("18001") == "180017"
    assert resolve_contracts(rows, enrollments) == {
        "a": ("UniSaoPaulo", "012530"),
        "b": ("UniSaoPaulo", "180157"),
        "c": ("UniSaoPaulo", "180017"),
    }


def test_never_by_payer() -> None:
    people = resolve_people([person("1", "11111111111"), person("2", "22222222222")], KEY)
    enrollments = resolve_enrollments(
        [enr("UniSaoPaulo", "180157", "1"), enr("UniSaoPaulo", "180017", "2")], people
    )
    rows = [  # mesmo customerId paga o irmão (180017); nada liga à matrícula 180157
        contract("sib", "C9", "1010", "PRESENCIAL", "18001-7"),
    ]
    out = resolve_contracts(rows, enrollments)
    assert out == {"sib": ("UniSaoPaulo", "180017")}
    assert enrollments[out["sib"]].person_key != enrollments[("UniSaoPaulo", "180157")].person_key


def test_unresolved_reported() -> None:
    stats = ResolutionStats()
    out = resolve_contracts([contract("x", "C1", "1010", "PRESENCIAL", "12ab")], {}, stats)
    assert out == {} and stats.unresolved == 1


def test_first_name_single_token() -> None:
    p = resolve_people([person("1", "11111111111", "ISIS C MELO")], KEY)
    assert p["11111111111"].first_name == "Isis"


def test_payer_types() -> None:
    people = resolve_people([person("1", "11111111111"), person("2", "22222222222")], KEY)
    enrollments = resolve_enrollments([enr("UniSaoPaulo", "180157", "1")], people)
    customers = [
        {"customerId": "A", "customerType": "INDIVIDUAL", "taxId": "11111111111"},
        {"customerId": "R", "customerType": "INDIVIDUAL", "taxId": "99999999999"},
        {"customerId": "E", "customerType": "COMPANY", "taxId": "40265100000164"},
    ]
    contracts = [contract(c, c, "1010", "PRESENCIAL", "18015") for c in "ARE"]
    payers = resolve_payers(customers, contracts, people, enrollments)
    assert payers["A"].payer_type == "aluno"
    assert payers["A"].person_key == people["11111111111"].person_key
    assert (payers["R"].payer_type, payers["R"].person_key) == ("responsavel", None)
    assert (payers["E"].payer_type, payers["E"].person_key) == ("empresa", None)


@pytest.mark.parametrize("area,suffix", [("PRESENCIAL", ""), ("DIGITAL", "_EAD")])
def test_brand_for_company(area: str, suffix: str) -> None:
    assert brand_for_company("1020", area) == "UniMontanhas" + suffix


def test_brand_for_company_csv() -> None:
    zpath = Path(__file__).parents[2] / "data" / "dados.zip"
    if not zpath.exists():
        pytest.skip("data/dados.zip ausente")
    with zipfile.ZipFile(zpath) as z:
        companies = list(csv.DictReader(io.TextIOWrapper(z.open("finance/company.csv"), "utf-8")))
        brands = {
            r["BRAND"] for r in csv.DictReader(io.TextIOWrapper(z.open("sag/brand.csv"), "utf-8"))
        }
    assert len(companies) == 5
    for c in companies:
        assert brand_for_company(c["companyCode"], "PRESENCIAL").lower() == c["tradeName"].lower()
        assert brand_for_company(c["companyCode"], "DIGITAL") in brands


def test_duplicate_brand_ra_from_two_cpfs_counted_not_overwritten() -> None:
    people = resolve_people([person("1", "10285495046"), person("2", "39053344705")], KEY)
    stats = ResolutionStats()
    out = resolve_enrollments(
        [enr("UniSaoPaulo", "181722", "1"), enr("UniSaoPaulo", "181722", "2")], people, stats
    )
    assert out[("UniSaoPaulo", "181722")].person_key == people["10285495046"].person_key
    assert stats.duplicates == 1
