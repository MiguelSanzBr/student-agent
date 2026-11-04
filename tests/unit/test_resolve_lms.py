import csv
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from etl.resolve_core import Enrollment
from etl.resolve_lms import is_student_login, resolve_lms_accounts, tenant_brand


def enr(brand: str, ra: str) -> tuple[tuple[str, str], Enrollment]:
    return (brand, ra), Enrollment(uuid4(), uuid4(), brand, ra, "ACTIVE")


def enrollments(*pairs: tuple[str, str]) -> dict[tuple[str, str], Enrollment]:
    return dict(enr(b, r) for b, r in pairs)


def user(uid: int, tenant: int, username: str, student_id: str = "") -> dict[str, str]:
    auth = "saml2" if student_id else "manual"
    return {
        "student_lms_id": str(uid),
        "tenant_id": str(tenant),
        "username": username,
        "student_id": student_id,
        "auth": auth,
    }


def login(uid: int, as_user: str) -> dict[str, str]:
    return {
        "eventname": "\\core\\event\\user_loggedin",
        "userid": str(uid),
        "other": json.dumps({"username": as_user}),
    }


def test_sso_trim_upper() -> None:
    e = enrollments(("UniSaoPaulo_EAD", "013825"))
    assert resolve_lms_accounts([user(1, 1, "ead_01382", " EAD_01382")], [], e) == {
        1: ("UniSaoPaulo_EAD", "013825")
    }


def test_other_tenant() -> None:
    e = enrollments(("UniSaoPaulo_EAD", "013825"), ("UniLitoral_EAD", "013825"))
    out = resolve_lms_accounts([user(1, 1, "a", "EAD_01382"), user(2, 3, "b", "EAD_01382")], [], e)
    assert out == {1: ("UniSaoPaulo_EAD", "013825"), 2: ("UniLitoral_EAD", "013825")}


def test_pres_prefix_is_presencial_brand() -> None:
    e = enrollments(("UniSaoPaulo", "181722"), ("UniSaoPaulo_EAD", "181722"))
    assert resolve_lms_accounts([user(1, 1, "p", "PRES_18172")], [], e) == {
        1: ("UniSaoPaulo", "181722")
    }


def test_manual_by_login_event() -> None:
    e = enrollments(("UniSaoPaulo_EAD", "011932"), ("UniMontanhas_EAD", "011932"))
    out = resolve_lms_accounts(
        [user(5, 1, "mariavitoria.araujo25")],
        [login(5, "ead_01193"), login(5, "ead_01193")],
        e,
    )
    assert out == {5: ("UniSaoPaulo_EAD", "011932")}


def test_manual_two_enrollments_by_login_not_name() -> None:
    e = enrollments(("UniSaoPaulo", "011843"), ("UniSaoPaulo", "182915"))
    out = resolve_lms_accounts(
        [user(102128, 1, "mariajulia.rios51")], [login(102128, "pres_18291")], e
    )
    assert out == {102128: ("UniSaoPaulo", "182915")}


def test_non_students_dropped() -> None:
    e = enrollments(("UniSaoPaulo_EAD", "011932"))
    users = [
        user(i, 1, n)
        for i, n in enumerate(["prof40013", "aluno.teste3", "admin", "integration.sag"])
    ]
    logs = [login(i, "ead_01193") for i in range(4)]
    assert resolve_lms_accounts(users, logs, e) == {}
    assert not any(
        is_student_login(n) for n in ["prof40013", "aluno.teste3", "admin", "integration.sag"]
    )
    assert is_student_login("mariavitoria.araujo25") and is_student_login("EAD_01382")


def test_ambiguous_manual_not_linked() -> None:
    e = enrollments(("UniSaoPaulo_EAD", "011932"), ("UniSaoPaulo_EAD", "012815"))
    users = [user(5, 1, "maria.silva10")]
    assert resolve_lms_accounts(users, [login(5, "ead_01193"), login(5, "ead_01281")], e) == {}
    assert resolve_lms_accounts(users, [], e) == {}  # sem evento
    assert resolve_lms_accounts(users, [login(5, "ead_09999")], e) == {}  # sem matrícula


def test_tenant_brand_matches_csv() -> None:
    zpath = Path(__file__).parents[2] / "data" / "dados.zip"
    if not zpath.exists():
        pytest.skip("data/dados.zip ausente")
    with zipfile.ZipFile(zpath) as z:
        rows = list(csv.DictReader(io.TextIOWrapper(z.open("lms/mdl_tenant.csv"), "utf-8")))
        brands = {
            r["BRAND"] for r in csv.DictReader(io.TextIOWrapper(z.open("sag/brand.csv"), "utf-8"))
        }
    assert len(rows) == 5
    for t in rows:
        assert tenant_brand(int(t["id"]), "PRES").lower() == t["slug"]
        assert tenant_brand(int(t["id"]), "EAD") in brands


def test_real_data_linked_accounts() -> None:
    zpath = Path(__file__).parents[2] / "data" / "dados.zip"
    if not zpath.exists():
        pytest.skip("data/dados.zip ausente")
    from etl.resolve_core import resolve_enrollments, resolve_people

    def rows(name: str) -> list[dict[str, str]]:
        with zipfile.ZipFile(zpath) as z:
            return list(csv.DictReader(io.TextIOWrapper(z.open(name), "utf-8", newline="")))

    people = resolve_people(rows("sag/person.csv"), b"k" * 32)
    enrollments = resolve_enrollments(rows("sag/enrollment.csv"), people)
    users = rows("lms/mdl_user.csv")
    out = resolve_lms_accounts(users, rows("lms/mdl_logstore_standard_log.csv"), enrollments)
    manual = {int(u["student_lms_id"]) for u in users if not u["student_id"].strip()}
    assert len(out) == 1003
    assert len(out.keys() - manual) == 983 and len(out.keys() & manual) == 20
    assert out[102128] == ("UniSaoPaulo", "182915")  # golden 9
