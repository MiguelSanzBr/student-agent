import json
import re
from pathlib import Path

import psycopg

from etl.load import LoadReport
from etl.report import lms_sql_facts
from evals import oracle as o
from tests.integration.conftest import GOLDEN, Loaded

TODAY = o.REFERENCE


def scoped(loaded: Loaded, person: str) -> psycopg.Connection:
    c = loaded.db.connect("agent_app", autocommit=False)
    c.execute("SELECT set_config('app.person_key', %s, true)", (person,))
    return c


def one(c: psycopg.Connection, sql: str, *args: object) -> tuple[object, ...]:
    row = c.execute(sql, args).fetchone()  # type: ignore[call-overload]
    assert row
    return row


FINANCE = """
WITH i AS (
  SELECT i.invoice_id, i.due_date,
    i.net_cents - coalesce((SELECT sum(principal_cents) FROM canon.payment p
        WHERE p.invoice_id = i.invoice_id AND p.status = 'CONFIRMED'), 0) AS bal,
    coalesce((SELECT sum(fine_cents + interest_cents) FROM canon.late_charge l
        WHERE l.invoice_id = i.invoice_id AND NOT coalesce(l.waived, false)), 0)
    - coalesce((SELECT sum(fine_cents + interest_cents) FROM canon.payment p
        WHERE p.invoice_id = i.invoice_id AND p.status = 'CONFIRMED'), 0) AS chg
  FROM canon.invoice i
  WHERE i.enrollment_key = %s AND i.status IN ('OPEN', 'OVERDUE', 'PARTIAL')
)
SELECT coalesce(sum(bal + chg), 0)::bigint, count(*) FROM i WHERE due_date < %s
"""


def test_sql_counts_equal_memory_counts_before_rls(loaded_db: Loaded) -> None:
    r = loaded_db.report
    assert r.sql_counts == r.memory_counts
    assert r.memory_counts["person"] > 0 and r.memory_counts["protocol"] > 0


def test_load_matches_oracle(loaded_db: Loaded) -> None:
    raw = o.load_csv(loaded_db.source)
    cases = json.loads((loaded_db.out / "golden_login.json").read_text())["cases"]
    login = {c["case"]: c["person_key"] for c in cases}
    assert len(login) == 10
    for case in json.loads(GOLDEN.read_text())["cases"]:
        enr = (case["brand"], case["ra"])
        with scoped(loaded_db, login[case["case"]]) as c:
            (ek,) = one(
                c, "SELECT enrollment_key FROM canon.enrollment WHERE brand=%s AND ra=%s", *enr
            )
            total, vencidas = one(c, FINANCE, ek, TODAY)
            f = o.oracle_financial(raw, enr)
            assert (total, vencidas) == (f.total_para_quitar, f.faturas_vencidas), case["case"]
            m = lms_sql_facts(c, TODAY, str(ek))
            lms = o.oracle_lms(raw, enr)
            assert m["tentativas"] == lms.tentativas, case["case"]
            assert m["simulados_abertos"] == lms.simulados_abertos, case["case"]
            assert m["simulados_com_tentativa_restante"] == lms.simulados_com_tentativa_restante
            assert m["encerradas_sem_tentativa"] == lms.encerradas_sem_tentativa
            rows = c.execute("SELECT protocol FROM canon.protocol WHERE enrollment_key=%s", (ek,))
            ids = {r[0] for r in rows.fetchall()}
            assert ids == {p["protocolo"] for p in o.oracle_protocols(raw, enr)}, case["case"]


def test_report_from_memory_matches_oracle(loaded_db: Loaded) -> None:
    r: LoadReport = loaded_db.report
    assert r.checks and all(c.memory == c.oracle for c in r.checks), [
        c for c in r.checks if c.memory != c.oracle
    ]
    md = (loaded_db.out / "data-quality.md").read_text()
    js = json.loads((loaded_db.out / "data-quality.json").read_text())
    assert "contagens SQL" in md and js["sql_counts"] == js["memory_counts"]
    assert not re.search(r"\b\d{11}\b", md + json.dumps(js))


def test_reload_keeps_agent_rw(loaded_db: Loaded) -> None:
    from etl.load import load_all
    from etl.raw import read_raw
    from tests.integration.conftest import etl_settings

    db = loaded_db.db
    with db.connect("postgres") as c:
        c.execute(
            "INSERT INTO agent_rw.feedback (trace_id, person_key, useful, comment) "
            "VALUES ('t1', gen_random_uuid(), true, 'x')"
        )
    load_all(read_raw(loaded_db.source), etl_settings(db), database=db.name)
    with db.connect("postgres") as c:
        assert one(c, "SELECT count(*) FROM agent_rw.feedback")[0] == 1
        assert one(c, "SELECT count(*) FROM canon.person")[0] > 0
        grants = one(
            c, "SELECT has_table_privilege('agent_app','agent_rw.feedback','INSERT'), "
               "has_table_privilege('agent_app','canon.person','SELECT')",
        )  # fmt: skip
        assert grants == (True, True)


def test_login_files_no_cpf(loaded_db: Loaded) -> None:
    files = [*loaded_db.out.iterdir(), *loaded_db.eval_out.iterdir()]
    assert {f.name for f in files} >= {"golden_login.json", "eval_students.json", "data-quality.md"}
    for f in files:
        assert not re.search(r"\b\d{11}\b", f.read_text()), f.name
    rows = json.loads((loaded_db.eval_out / "eval_students.json").read_text())
    assert rows and set(rows[0]) == {
        "person_key", "modalidade", "ativo", "n_matriculas", "in_collection", "tem_acordo",
        "protocolo_aberto", "lms_manual", "transferido", "payer_type",
    }  # fmt: skip
    assert Path(loaded_db.out / "golden_login.json").exists()
