import csv
import io
import zipfile
from pathlib import Path

import pytest

from core.ontology import Ontology
from etl.raw import read_raw

pytestmark = pytest.mark.fulldata
ROOT = Path(__file__).resolve().parents[2]
ZIP = ROOT / "data" / "dados.zip"


@pytest.fixture(scope="module")
def raw():  # type: ignore[no-untyped-def]
    if not ZIP.exists():
        pytest.skip("data/dados.zip ausente")
    return read_raw(ZIP)


@pytest.fixture(scope="module")
def onto(raw) -> Ontology:  # type: ignore[no-untyped-def]
    classes = {r["CLASS_ID"]: r["SUBJECT_CODE"] for r in raw["sag/class_section"]}
    return Ontology.load(ROOT / "ontology", class_subjects=classes)


def test_full_data_matches_ontology_columns(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    assert len(raw) == 47
    cols = {(t, c) for t, rows in raw.items() for c in rows[0]}
    assert len(cols) == 401
    assert cols == {(t, c) for t in onto.tables() for c in onto.table_columns(t)}


def test_enum_labels_full(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    checked = 0
    for t in onto.tables():
        for c in onto.table_columns(t):
            spec = onto.column(t, c)
            if spec.destination not in ("load", "transform") or spec.type != "enum":
                continue
            assert spec.target
            enum = onto.field_enum(*spec.target.split("."))
            assert enum, (t, c)
            for v in {r[c] for r in raw[t]} - {""}:
                onto.label_pt(enum, v)  # KeyError = valor sem rótulo PT
                checked += 1
    assert checked > 100


def test_messages_loaded_100pct_typed(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    rows = [
        r
        for r in raw["secretary/protocol_interaction"]
        if r["Author_Type"] in ("Analyst", "System") and r["Is_Internal_Note"] == "False"
    ]
    assert len(rows) > 9000
    assert all(onto.classify_message(r["Message"]) for r in rows)


def test_active_rooms_100pct_typed(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    active = {r["courseid"] for r in raw["lms/mdl_user_enrolments"] if r["status"] == "0"}
    rooms = [r for r in raw["lms/mdl_course"] if r["id"] in active]
    assert len(rooms) > 9000
    assert all(onto.room_subject(r["shortname"], r["idnumber"]) for r in rooms)
    assert all(onto.room_subject(r["shortname"], None) for r in rooms)  # só pelo prefixo


def test_quizzes_100pct_typed(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    assert all(onto.quiz_type(r["name"]) for r in raw["lms/mdl_quiz"])


def test_frozen_lists_match_csv(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    assert set(onto.professor_names()) == {r["FULL_NAME"] for r in raw["sag/professor"]}
    assert set(onto.unit_ids()) == {r["UNIT_ID"] for r in raw["sag/unit"]}
    codes = {r["SUBJECT_CODE"] for r in raw["sag/subject"]}
    assert all(onto.subject_name_pt(c) for c in codes)
    assert {r["COURSE"] for r in raw["sag/course"]} == {
        c for c in ("110", "120", "130", "140", "210", "220", "230", "240")
    }


def test_services_match_demand_type(raw, onto: Ontology) -> None:  # type: ignore[no-untyped-def]
    demand = {r["Demand_Code"]: r for r in raw["secretary/demand_type"]}
    assert set(demand) == set(onto.service_codes())
    for c, r in demand.items():
        s = onto.service(c)
        assert s.sla_hours == int(r["SLA_Hours"])
        assert s.fee_cents == int(r["Service_Fee"].replace("R$ ", "").replace(",", ""))
    seen: dict[str, set[str]] = {}
    pt = {"Portal": "Portal", "WhatsApp": "WhatsApp", "Email": "E-mail", "Phone": "Telefone"}
    pt["Walk-in"] = "Presencial"
    for r in raw["secretary/protocol"]:
        seen.setdefault(r["Demand"], set()).add(pt[r["Channel"]])
    for c in demand:
        assert set(onto.service(c).channels) == seen[c], c


def test_zip_and_csv_agree() -> None:
    if not ZIP.exists():
        pytest.skip("data/dados.zip ausente")
    with zipfile.ZipFile(ZIP) as z:
        with io.TextIOWrapper(z.open("sag/brand.csv"), encoding="utf-8", newline="") as f:
            assert next(csv.reader(f))[0] == "BRAND"
