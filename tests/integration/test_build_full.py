import re
from pathlib import Path

import pytest

from etl.build import build_all, build_counts
from etl.raw import read_raw
from evals import oracle as o

pytestmark = pytest.mark.fulldata
ZIP = Path(__file__).resolve().parents[2] / "data" / "dados.zip"


@pytest.fixture(scope="module")
def tables() -> dict[str, list[dict[str, object]]]:
    if not ZIP.exists():
        pytest.skip("data/dados.zip ausente")
    return build_all(read_raw(ZIP), b"k" * 32)  # type: ignore[return-value]


def test_counts_against_oracle(tables: dict[str, list[dict[str, object]]]) -> None:
    raw = o.load_csv(ZIP)
    c = build_counts(tables)
    assert c["protocol"] == sum(1 for r in o.oracle_link_protocols(raw).values() if r.rule) == 3911
    assert c["lms_account"] == len(o.oracle_lms_accounts(raw)) == 1003
    assert c["enrollment"] == len(o.enrollments(raw))
    assert c["contract"] == 1065


def test_29_analyst_logins_absent(tables: dict[str, list[dict[str, object]]]) -> None:
    raw = o.load_csv(ZIP)
    logins = {r["Author"] for r in raw["secretary/protocol_interaction"]
              if r["Author_Type"] != "Requester"}  # fmt: skip
    logins |= {r["Demand_Analyst"] for r in raw["secretary/protocol"]}
    logins |= {r["Changed_By"] for r in raw["secretary/protocol_status_history"]}
    logins |= {r["Signed_By"] for r in raw["secretary/issued_document"]}
    logins = {x.lower() for x in logins if x and x.lower() != "system"}
    assert len(logins) == 29
    rx = re.compile(r"\b(" + "|".join(map(re.escape, logins)) + r")\b", re.I)
    cells = (str(v) for rows in tables.values() for r in rows for v in r.values() if v is not None)
    assert not [c for c in cells if rx.search(c)]


def test_coverage_dates(tables: dict[str, list[dict[str, object]]]) -> None:
    cov = {c["domain"]: str(c["starts_on"]) for c in tables["coverage"]}
    assert cov["matricula_eventos"] == "2020-12-12" and cov["lms"] == "2023-01-15"
    assert cov["secretaria"] == "2022-03-01" and cov["financeiro"] == "2021-01-01"
