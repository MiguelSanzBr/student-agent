import re
import uuid
from pathlib import Path

import psycopg
import pytest
import yaml
from psycopg import errors

from core.ontology import Ontology
from tests.integration.conftest import ETL, Db

A, B = str(uuid.uuid4()), str(uuid.uuid4())
LITORAL, SP = "UniLitoral", "UniSaoPaulo"


def seed(db: Db) -> None:
    """Duas pessoas e dois preços de famílias diferentes, carregados por COPY como etl_owner."""
    with db.connect() as c:
        for table, cols, rows in (
            ("person", "person_key, first_name", [(A, "Ana"), (B, "Beto")]),
            (
                "price",
                "brand_family, course_id, ano, mensalidade",
                [(SP, "120", 2026, 90000), (LITORAL, "120", 2026, 70000)],
            ),
        ):
            with c.cursor().copy(f"COPY canon.{table} ({cols}) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row(r)


def scoped(
    db: Db, role: str, person: str | None = None, brands: str | None = None
) -> psycopg.Connection:
    c = db.connect(role, autocommit=False)
    if person:
        c.execute("SELECT set_config('app.person_key', %s, true)", (person,))
    if brands:
        c.execute("SELECT set_config('app.brands', %s, true)", (brands,))
    return c


def count(c: psycopg.Connection, table: str) -> int:
    row = c.execute(f"SELECT count(*) FROM {table}").fetchone()  # type: ignore[call-overload]
    assert row
    return int(row[0])


def test_copy_then_rls_order_works(db: Db) -> None:
    seed(db)
    db.apply("rls.sql")
    with scoped(db, "agent_app", A) as c:
        assert count(c, "canon.person") == 1


def test_owner_reads_zero_after_force(db: Db) -> None:
    seed(db)
    db.apply("rls.sql")
    with db.connect() as c:
        assert count(c, "canon.person") == 0


def test_copy_after_force_fails(db: Db) -> None:
    db.apply("rls.sql")
    with pytest.raises(errors.FeatureNotSupported):  # COPY não funciona com RLS ativo
        seed(db)


def test_no_setting_nothing(db: Db) -> None:
    seed(db)
    db.apply("rls.sql")
    with scoped(db, "agent_app") as c:
        assert count(c, "canon.person") == 0
        assert count(c, "canon.price") == 0


def test_other_person_nothing(db: Db) -> None:
    seed(db)
    db.apply("rls.sql")
    with scoped(db, "agent_app", A) as c:
        assert c.execute("SELECT first_name FROM canon.person").fetchall() == [("Ana",)]
        assert count(c, f"canon.person WHERE person_key = '{B}'") == 0


def test_catalog_family_scope(db: Db) -> None:
    seed(db)
    db.apply("rls.sql")
    with scoped(db, "agent_app", A, "{UniSaoPaulo}") as c:
        assert c.execute("SELECT brand_family FROM canon.price").fetchall() == [(SP,)]


INSERT_REQ = (
    "INSERT INTO agent_rw.protocol_request (id, person_key, enrollment_index, service_code, "
    "draft_id, opened_on, prazo) VALUES (%s, %s, '1', %s, %s, '2026-11-03', '2026-11-10')"
)


def test_insert_other_person_rejected(db: Db) -> None:
    db.apply("rls.sql")
    with scoped(db, "agent_app", A) as c:
        c.execute(INSERT_REQ, ("SA-2026-000001", A, "DOC-001", "d1"))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute(INSERT_REQ, ("SA-2026-000002", B, "DOC-001", "d2"))


def test_check_rejects_service(db: Db) -> None:
    db.apply("rls.sql")
    with scoped(db, "agent_app", A) as c, pytest.raises(errors.CheckViolation):
        c.execute(INSERT_REQ, ("SA-2026-000001", A, "ACA-012", "d1"))


def test_check_matches_ontology_agent_services(db: Db) -> None:
    onto = Ontology.load(ETL.parent / "ontology")
    expected = {c for c in onto.service_codes() if onto.service(c).mode == "agent"}
    with db.connect() as c:
        row = c.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'agent_rw.protocol_request'::regclass AND contype = 'c'"
        ).fetchone()
    assert row and set(re.findall(r"[A-Z]{3}-\d{3}", row[0])) == expected


def test_roles_flags(db: Db) -> None:
    with db.connect() as c:
        rows = c.execute(
            "SELECT rolname, rolsuper, rolbypassrls, "
            "(SELECT count(*) FROM pg_auth_members WHERE member = r.oid) "
            "FROM pg_roles r WHERE rolname IN ('etl_owner', 'agent_app', 'ops_audit')"
        ).fetchall()
    assert len(rows) == 3
    assert all(not su and not byp and m == 0 for _, su, byp, m in rows)


def grants_yaml() -> dict[str, dict[str, dict[str, list[str]]]]:
    return yaml.safe_load((ETL / "grants.yaml").read_text())


def test_grants_match_allowlist(db: Db) -> None:
    db.apply("rls.sql")
    want = grants_yaml()
    with db.connect() as c:
        tables = {
            (s, t)
            for s, t in c.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_schema IN ('canon', 'agent_rw')"
            ).fetchall()
        }
        expected = {
            (role, s, t, p.upper())
            for role, schemas in want.items()
            for s, spec in schemas.items()
            for t0, privs in spec.items()
            for t in ([x for sc, x in tables if sc == s] if t0 == "*" else [t0])
            for p in privs
        }
        got = {
            tuple(r)
            for r in c.execute(
                "SELECT grantee, table_schema, table_name, privilege_type "
                "FROM information_schema.role_table_grants "
                "WHERE table_schema IN ('canon', 'agent_rw') AND grantee <> 'etl_owner'"
            ).fetchall()
        }
    assert got == expected
    assert not {p for r, _, _, p in got if r == "agent_app"} & {"TRUNCATE", "DELETE", "UPDATE"}


def non_global(db: Db) -> list[str]:
    onto_entities = yaml.safe_load((ETL.parent / "ontology" / "entities.yaml").read_text())
    scoped_tables = {t for t, v in onto_entities.items() if v["scope"] != "global"}
    return sorted(scoped_tables | {"protocol_request", "feedback", "turn_output"})


def test_force_rls_on_all_non_global_tables(db: Db) -> None:
    db.apply("rls.sql")
    with db.connect() as c:
        rows = c.execute(
            "SELECT n.nspname, c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname IN ('canon', 'agent_rw') AND c.relkind = 'r'"
        ).fetchall()
    forced = {r for _, r, rls, force in rows if rls and force}
    assert forced == set(non_global(db))
    assert {r for _, r, _, _ in rows} >= forced  # global fica sem RLS


def test_agent_rw_has_no_fk_to_canon(db: Db) -> None:
    with db.connect() as c:
        n = c.execute(
            "SELECT count(*) FROM pg_constraint WHERE contype = 'f' AND "
            "(connamespace = 'agent_rw'::regnamespace OR confrelid IN "
            "(SELECT oid FROM pg_class WHERE relnamespace = 'canon'::regnamespace))"
        ).fetchone()
    assert n == (0,)


def agent_policy_exprs(c: psycopg.Connection) -> list[str]:
    return [
        e
        for r in c.execute(
            "SELECT pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
            "FROM pg_policy WHERE polrelid IN "
            "(SELECT oid FROM pg_class WHERE relnamespace = 'agent_rw'::regnamespace)"
        ).fetchall()
        for e in r
        if e
    ]


def test_agent_rw_policies_reference_no_canon(db: Db) -> None:
    db.apply("rls.sql")
    with db.connect() as c:
        exprs = agent_policy_exprs(c)
    assert exprs and not [e for e in exprs if "canon." in e]
    assert all("util." in e or "app.now" in e for e in exprs)


def test_drop_schema_canon_cascade_preserves_agent_rw_policies(db: Db) -> None:
    db.apply("rls.sql")

    def n() -> int:
        with db.connect() as c:
            return count(c, "pg_policies WHERE schemaname = 'agent_rw'")

    before = n()
    with db.connect() as c:
        c.execute("DROP SCHEMA canon CASCADE")
    assert before > 0 and n() == before
    with scoped(db, "agent_app", A) as c:
        c.execute(INSERT_REQ, ("SA-2026-000001", A, "DOC-001", "d1"))
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute(INSERT_REQ, ("SA-2026-000002", B, "DOC-001", "d2"))
    with scoped(db, "agent_app", B) as c:
        assert count(c, "agent_rw.protocol_request") == 0


def test_canon_rebuild_preserves_agent_rw(db: Db) -> None:
    db.apply("rls.sql")
    with scoped(db, "agent_app", A) as c:
        c.execute(INSERT_REQ, ("SA-2026-000001", A, "DOC-001", "d1"))
        c.commit()
    db.apply("util.sql", "schema.sql")  # recarga
    db.apply("rls.sql")
    with scoped(db, "agent_app", A) as c:
        assert count(c, "agent_rw.protocol_request") == 1
        assert count(c, "canon.person") == 0  # canon recriado e vazio


def test_turn_output_expired_invisible_to_ops_audit(db: Db) -> None:
    db.apply("rls.sql")
    ins = "INSERT INTO agent_rw.turn_output VALUES (%s, %s, %s, %s, '{}')"
    with scoped(db, "agent_app", A) as c:
        c.execute(ins, ("t-old", A, "2026-10-01T00:00Z", "2026-10-08T00:00Z"))
        c.execute(ins, ("t-new", A, "2026-11-01T00:00Z", "2026-11-08T00:00Z"))
        c.commit()
    with scoped(db, "agent_app", A) as c, pytest.raises(errors.InsufficientPrivilege):  # só INSERT
        c.execute("SELECT * FROM agent_rw.turn_output")
    with db.connect("ops_audit", autocommit=False) as c:
        c.execute("SELECT set_config('app.now', '2026-11-03T00:00Z', true)")
        assert c.execute("SELECT trace_id FROM agent_rw.turn_output").fetchall() == [("t-new",)]
    # purga: etl_owner só enxerga e apaga as expiradas
    with db.connect("etl_owner", autocommit=False) as c:
        c.execute("SELECT set_config('app.now', '2026-11-03T00:00Z', true)")
        assert c.execute("DELETE FROM agent_rw.turn_output WHERE true").rowcount == 1
        c.commit()
    with db.connect("postgres") as c:
        assert c.execute("SELECT trace_id FROM agent_rw.turn_output").fetchall() == [("t-new",)]


def test_schema_matches_ontology() -> None:
    """Toda coluna de destino da ontologia existe em schema.sql e o schema não tem coluna extra."""
    sql = (ETL / "schema.sql").read_text()
    cols: dict[str, set[str]] = {
        m[1]: {ln.split()[0] for ln in m[2].strip().splitlines()}
        for m in re.finditer(r"create table canon\.(\w+) \((.*?)\n\);", sql, re.S | re.I)
    }
    onto = Ontology.load(Path(ETL.parent / "ontology"))
    ent = yaml.safe_load((ETL.parent / "ontology" / "entities.yaml").read_text())
    assert set(cols) == set(ent)
    for t, c in cols.items():
        fields = set(ent[t]["fields"])
        implicit = {"person_key", "enrollment_key", "brand_family"} - fields
        assert c - implicit == fields, t
    for tbl in onto.tables():
        for col in onto.table_columns(tbl):
            target = onto.column(tbl, col).target
            if target:
                t, f = target.split(".")
                assert f in cols[t], target
