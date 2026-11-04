import csv
import io
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import pytest

from core.normalize import ra_check_digit
from etl.resolve_core import (
    resolve_enrollments,
    resolve_payers,
    resolve_people,
)
from etl.resolve_secretary import ProtocolLink, _brand, protocol_number, resolve_protocols
from evals.oracle import load_csv, oracle_link_protocols

KEY = b"k" * 32


def ra(base: str) -> str:
    return base + ra_check_digit(base)


def world(
    people: list[tuple[str, str]],
    enrollments: list[tuple[str, str, str, str, str]],
    customers: list[tuple[str, str]] = (),  # type: ignore[assignment]
    contracts: list[tuple[str, str, str]] = (),  # type: ignore[assignment]
):  # type: ignore[no-untyped-def]
    """people: (pid, cpf); enrollments: (brand, ra, pid, status, status_date);
    customers: (customerId, cpf); contracts: (customerId, companyCode+area, academicRef)."""
    prow = [{"PERSON_ID": p, "CPF": c, "FULL_NAME": "A B"} for p, c in people]
    ppl = resolve_people(prow, KEY)
    enr = resolve_enrollments(
        [
            {
                "BRAND": b,
                "STUDENT_ID": r,
                "PERSON_ID": p,
                "ENROLLMENT_STATUS": s,
                "STATUS_DATE": d,
            }
            for b, r, p, s, d in enrollments
        ],
        ppl,
    )
    crow = [{"customerId": i, "customerType": "INDIVIDUAL", "taxId": c} for i, c in customers]
    krow = [
        {
            "contractId": f"CT{n}",
            "customerId": i,
            "companyCode": cc[:4],
            "businessArea": cc[4:],
            "academicRef": ref,
        }
        for n, (i, cc, ref) in enumerate(contracts)
    ]
    return ppl, enr, crow, krow, resolve_payers(crow, krow, ppl, enr)


def proto(
    n: str, stu: str, cpf: str, typ: str = "Student", unit: str = "SPA-PAU", day: str = "2025-01-01"
) -> dict[str, str]:
    return {
        "Protocol": n,
        "Stu_ID": stu,
        "Requester_CPF": cpf,
        "Requester_Type": typ,
        "Unit_Code": unit,
        "Opened_At": f"{day}T10:00:00Z",
    }


def run(w, rows):  # type: ignore[no-untyped-def]
    ppl, enr, crow, krow, payers = w
    return resolve_protocols(rows, ppl, enr, krow, payers, customers=crow)


CPF = "59355611587"


def test_stu_id_without_zeros() -> None:
    r = ra("01281")
    w = world(
        [("1", CPF)],
        [
            ("UniAtlantico_EAD", r, "1", "ACTIVE", "2024-01-01"),
            ("UniLitoral_EAD", r, "1", "ACTIVE", "2024-01-01"),
        ],
    )
    out = run(w, [proto("P1", "12815", CPF, unit="ATL-SAO-EAD")])
    assert out["P1"].rule == "ra_cpf"
    assert out["P1"].enrollment == ("UniAtlantico_EAD", "012815")


def test_typo_by_cpf() -> None:
    w = world([("1", CPF)], [("UniSaoPaulo", ra("18223"), "1", "ACTIVE", "2024-01-01")])
    out = run(w, [proto("P1", "182203", CPF)])
    assert out["P1"] == ProtocolLink(("UniSaoPaulo", "182230"), "cpf_brand_unique")


def test_cpf_mismatch_not_by_ra() -> None:
    w = world(
        [("1", CPF), ("2", "39053344705")],
        [("UniSaoPaulo", ra("18001"), "1", "ACTIVE", "2024-01-01")],
    )
    assert run(w, [proto("P1", ra("18001"), "39053344705")]) == {}


def _guardian_world(second_enrollment: bool = False):  # type: ignore[no-untyped-def]
    es = [("UniSaoPaulo", ra("18001"), "1", "ACTIVE", "2024-01-01")]
    ct = [("C1", "1010PRESENCIAL", ra("18001"))]
    if second_enrollment:
        es.append(("UniSaoPaulo", ra("18002"), "2", "ACTIVE", "2024-01-01"))
        ct.append(("C1", "1010PRESENCIAL", ra("18002")))
    return world(
        [("1", CPF), ("2", "39053344705")], es, customers=[("C1", "52998224725")], contracts=ct
    )


def test_guardian_payer_with_stu_id() -> None:
    out = run(_guardian_world(), [proto("P1", ra("18001"), "52998224725", "Guardian")])
    assert out["P1"] == ProtocolLink(("UniSaoPaulo", ra("18001")), "guardian_payer")


def test_guardian_payer_unique_without_stu_id_linked() -> None:
    out = run(_guardian_world(), [proto("P1", "", "52998224725", "Guardian")])
    assert out["P1"] == ProtocolLink(("UniSaoPaulo", ra("18001")), "guardian_payer_unique")


def test_guardian_paying_two_not_linked() -> None:
    assert run(_guardian_world(True), [proto("P1", "", "52998224725", "Guardian")]) == {}


def test_guardian_stu_id_of_unpaid_enrollment_not_linked() -> None:
    assert run(_guardian_world(), [proto("P1", ra("18002"), "52998224725", "Guardian")]) == {}


def test_third_party_not_linked() -> None:
    w = _guardian_world()
    assert run(w, [proto("P1", "", "52998224725", "Third Party")]) == {}


def test_cpf_brand_by_date() -> None:
    w = world(
        [("1", CPF)],
        [
            ("UniSaoPaulo_EAD", ra("01150"), "1", "GRADUATED", "2023-01-04"),
            ("UniSaoPaulo_EAD", ra("01229"), "1", "ACTIVE", "2023-08-02"),
        ],
    )
    out = run(
        w,
        [
            proto("P1", "", CPF, unit="SPA-SAO-EAD", day="2022-07-11"),
            proto("P2", "", CPF, unit="SPA-SAO-EAD", day="2025-01-01"),
        ],
    )
    assert out["P1"] == ProtocolLink(("UniSaoPaulo_EAD", ra("01150")), "cpf_brand_by_date")
    assert out["P2"] == ProtocolLink(("UniSaoPaulo_EAD", ra("01229")), "cpf_brand_by_date")


def _two_enrollments(d1: str, d2: str, s1: str = "GRADUATED", s2: str = "ACTIVE"):  # type: ignore[no-untyped-def]
    return world(
        [("1", CPF)],
        [
            ("UniSaoPaulo_EAD", ra("01150"), "1", s1, d1),
            ("UniSaoPaulo_EAD", ra("01229"), "1", s2, d2),
        ],
    )


def _by_date(w, day: str):  # type: ignore[no-untyped-def]
    return run(w, [proto("P", "", CPF, unit="SPA-SAO-EAD", day=day)])["P"].enrollment[1]


def test_by_date_between_two_enrollments() -> None:
    w = _two_enrollments("2023-01-04", "2023-08-02")
    assert _by_date(w, "2023-01-04") == ra("01150")  # no dia em que terminou ainda vigora
    assert _by_date(w, "2023-01-05") == ra("01229")  # entre o fim de uma e a outra: a seguinte


def test_by_date_equal_status_date_tie_prefers_ended_enrollment() -> None:
    # transferência: mesma data; até esse dia vigorava a encerrada (não-ACTIVE), depois a ACTIVE.
    w = _two_enrollments("2022-07-30", "2022-07-30", "TRANSFERRED", "ACTIVE")
    assert _by_date(w, "2022-07-30") == ra("01150")
    assert _by_date(w, "2022-08-01") == ra("01229")
    w2 = _two_enrollments("2022-07-30", "2022-07-30", "ACTIVE", "ACTIVE")  # empate total: menor RA
    assert _by_date(w2, "2022-01-01") == ra("01150")


def test_by_date_empty_status_date_is_open_ended() -> None:
    w = _two_enrollments("2023-01-04", "")
    assert _by_date(w, "2030-01-01") == ra("01229")


def test_by_date_non_iso_date_fails_loudly() -> None:
    w = _two_enrollments("2023-01-04", "2023-08-02")
    with pytest.raises(ValueError):
        _by_date(w, "11/07/2022")


def test_invariant_student_or_payer() -> None:
    w = _guardian_world(True)
    rows = [
        proto("P1", ra("18001"), CPF),
        proto("P2", ra("18001"), "52998224725", "Guardian"),
        proto("P3", "", "52998224725", "Guardian"),
        proto("P4", ra("18002"), "52998224725", "Guardian"),
        proto("P5", ra("18001"), "39053344705"),
    ]
    out = run(w, rows)
    assert out
    ppl, enr, _, _, _ = w
    cpf_of = {p.person_key: c for c, p in ppl.items()}
    for n, link in out.items():
        cpf = next(r["Requester_CPF"] for r in rows if r["Protocol"] == n)
        owner = cpf_of[enr[link.enrollment].person_key]
        assert cpf == owner or (cpf == "52998224725" and link.rule.startswith("guardian"))


def test_protocol_number_format() -> None:
    assert protocol_number("PRT-2022/0003006") == "20220003006"


def test_unit_prefix_map_matches_csv() -> None:
    zpath = Path(__file__).parents[2] / "data" / "dados.zip"
    if not zpath.exists():
        pytest.skip("data/dados.zip ausente")
    with zipfile.ZipFile(zpath) as z:
        units = list(csv.DictReader(io.TextIOWrapper(z.open("secretary/unit.csv"), "utf-8")))
        brands = {
            r["BRAND"] for r in csv.DictReader(io.TextIOWrapper(z.open("sag/brand.csv"), "utf-8"))
        }

    def slug(v: str) -> str:
        v = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode()
        return "".join(ch for ch in v.lower() if ch.isalnum())

    fam = {slug(b): b for b in brands if not b.endswith("_EAD")}
    assert len(units) == 27
    for u in units:
        want = fam[slug(u["Brand_Name"])] + ("_EAD" if u["Unit_Code"].endswith("-EAD") else "")
        assert _brand(u["Unit_Code"]) == want


def test_real_data_counts() -> None:
    zpath = Path(__file__).parents[2] / "data" / "dados.zip"
    if not zpath.exists():
        pytest.skip("data/dados.zip ausente")

    def rows(name: str) -> list[dict[str, str]]:
        with zipfile.ZipFile(zpath) as z:
            return list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8")))

    ppl = resolve_people(rows("sag/person.csv"), KEY)
    enr = resolve_enrollments(rows("sag/enrollment.csv"), ppl)
    cust, con = rows("finance/customer.csv"), rows("finance/contract.csv")
    payers = resolve_payers(cust, con, ppl, enr)
    prot = rows("secretary/protocol.csv")
    out = resolve_protocols(prot, ppl, enr, con, payers, customers=cust)
    assert Counter(link.rule for link in out.values()) == {
        "ra_cpf": 3455,
        "guardian_payer": 73,
        "guardian_payer_unique": 29,
        "cpf_brand_unique": 337,
        "cpf_brand_by_date": 17,
    }
    assert len(out) == 3911 and len(prot) - len(out) == 22
    oracle = oracle_link_protocols(load_csv(zpath))  # independente (evals/oracle.py)
    by_date = {n: link for n, link in out.items() if link.rule == "cpf_brand_by_date"}
    assert len(by_date) == 17
    for n, link in out.items():
        assert (link.enrollment, link.rule) == (oracle[n].enrollment, oracle[n].rule), n
    refs = [r["PROTOCOL_REF"] for r in rows("sag/enrollment_status_history.csv")]
    known = {p["Protocol"] for p in prot}
    found = [x for x in refs if x and protocol_number(x) in known]
    assert len(found) == 229 == len([x for x in refs if x])
