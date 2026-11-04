"""Carga: tabelas -> COPY -> contagens SQL = memória -> relatório -> rls.sql -> commit.

Tudo numa transação como `etl_owner`. O FORCE RLS só vem depois do COPY e das contagens (depois dele
o dono lê 0 linhas de `canon`). CPF cru só existe em memória (chave do dict de pessoas): nunca
chega ao banco, ao relatório, ao log nem aos arquivos de saída.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

from app.config import AppSettings
from etl.build import (
    Resolution,
    Row,
    build_all,
    build_counts,
    resolve,
)
from etl.config import EtlSettings
from etl.raw import RawData
from etl.report import (
    Check,
    LoadReport,
    lms_sql_facts,
    logins_in_sql,
    protocol_ref_sql,
    snapshot_sql,
    write_report,
)
from etl.resolve_core import (
    ResolutionStats,
    resolve_contracts,
    resolve_enrollments,
    resolve_people,
)

__all__ = ["LoadReport", "load_all", "write_eval_students", "write_golden_login"]

ETL = Path(__file__).resolve().parent
TODAY: date = AppSettings.model_fields["reference_date"].default


def _sql(name: str) -> str:
    return (ETL / name).read_bytes().decode()


def sql_counts(conn: psycopg.Connection) -> dict[str, int]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'canon' AND table_type = 'BASE TABLE'"
        ).fetchall()
    ]
    out: dict[str, int] = {}
    for t in sorted(tables):
        row = conn.execute(f"SELECT count(*) FROM canon.{t}").fetchone()
        assert row
        out[t] = int(row[0])
    return out


def _copy(conn: psycopg.Connection, tables: Mapping[str, Sequence[Row]]) -> None:
    for table, rows in tables.items():
        if not rows:
            continue
        cols = list(rows[0])
        with conn.cursor().copy(f"COPY canon.{table} ({', '.join(cols)}) FROM STDIN") as cp:
            for r in rows:
                cp.write_row([r[c] for c in cols])


def _analyst_logins(raw: RawData) -> set[str]:
    out = {
        r["Author"]
        for r in raw["secretary/protocol_interaction"]
        if r["Author_Type"] != "Requester"
    }
    out |= {r["Demand_Analyst"] for r in raw["secretary/protocol"]}
    out |= {r["Changed_By"] for r in raw["secretary/protocol_status_history"]}
    out |= {r["Signed_By"] for r in raw["secretary/issued_document"]}
    return {x.lower() for x in out if x and x.lower() != "system"}


def _logins_present(logins: set[str], tables: Mapping[str, Sequence[Row]]) -> int:
    if not logins:
        return 0
    rx = re.compile(r"\b(" + "|".join(map(re.escape, logins)) + r")\b", re.I)
    found: set[str] = set()
    for rows in tables.values():
        for r in rows:
            for v in r.values():
                if isinstance(v, str) and (m := rx.search(v)):
                    found.add(m.group(1).lower())
    return len(found)


def _invariants(tables: Mapping[str, Sequence[Row]], counts: Mapping[str, int]) -> dict[str, bool]:
    people = {p["person_key"] for p in tables["person"]}
    enrollments = {e["enrollment_key"] for e in tables["enrollment"]}
    owned = {t: rows for t, rows in tables.items() if rows and "person_key" in rows[0]}
    return {
        "toda linha com person_key aponta para uma pessoa": all(
            r["person_key"] in people for rows in owned.values() for r in rows
        ),
        "toda linha com enrollment_key aponta para uma matrícula": all(
            r["enrollment_key"] in enrollments
            for rows in owned.values()
            for r in rows
            if "enrollment_key" in r
        ),
        "enrollment_key único": len(enrollments) == len(tables["enrollment"]),
        "nenhuma tabela canônica vazia além das esperadas": all(
            counts[t] > 0 for t in ("person", "enrollment", "protocol", "invoice", "lms_account")
        ),
    }


def _report(
    conn: psycopg.Connection,
    raw: RawData,
    res: Resolution,
    tables: Mapping[str, Sequence[Row]],
    mem: dict[str, int],
    sql: dict[str, int],
    lms: dict[str, int],
    key: bytes,
    oracle_raw: dict[str, list[dict[str, str]]] | None,
) -> LoadReport:
    st = {k: ResolutionStats() for k in ("pessoas", "matrículas", "contratos")}
    resolve_people(raw["sag/person"], key, st["pessoas"])
    resolve_enrollments(raw["sag/enrollment"], res.people, st["matrículas"])
    resolve_contracts(raw["finance/contract"], res.enrollments, st["contratos"])
    by_rule: dict[str, int] = dict(Counter(link.rule for link in res.links.values() if link.rule))
    protocols = {p["Protocol"] for p in raw["secretary/protocol"]}
    refs = [e["protocol_ref"] for e in tables["enrollment_event"] if e["protocol_ref"]]
    logins = _analyst_logins(raw)
    present = _logins_present(logins, tables)
    r = LoadReport(
        memory_counts=mem,
        sql_counts=sql,
        invariants=_invariants(tables, mem),
        resolution={
            k: {"unresolved": v.unresolved, "duplicates": v.duplicates} for k, v in st.items()
        },
        protocols_by_rule=by_rule,
        protocols_unlinked=len(protocols) - len(res.links),
        protocol_ref=(sum(1 for x in refs if x in protocols), len(refs)),
        lms=lms,
        logins_absent=len(logins) - present,
        logins_present=present,
        coverage=[
            {k: None if v is None else str(v) for k, v in c.items()} for c in tables["coverage"]
        ],
    )
    snap_rows = raw["finance/receivable_snapshot"]
    r.snapshot = snapshot_sql(conn, snap_rows)
    distinct = len(logins)
    cov: Mapping[str, Any] = {}
    facts: list[Any] = []
    o: Any = None
    if oracle_raw is not None:
        from evals import oracle as o

        cov = o.oracle_coverage(oracle_raw)
        facts = [o.oracle_lms(oracle_raw, k) for k in o.enrollments(oracle_raw)]

    def chk(name: str, memoria: Any, sql_leg: Any, oraculo: Any = None) -> Check:
        legs = {"memoria": memoria, "sql": sql_leg, "oraculo": oraculo}
        return Check(name, {k: v for k, v in legs.items() if v is not None})

    def orc(f: Any) -> Any:
        return f() if oracle_raw is not None else None

    sql_present = logins_in_sql(conn, logins)
    r.checks = [
        chk(
            "pessoas",
            mem["person"],
            sql["person"],
            orc(lambda: len(set(o.person_cpf(oracle_raw).values()))),
        ),
        chk("matrículas", mem["enrollment"], sql["enrollment"], cov.get("matriculas")),
        chk(
            "contratos resolvidos",
            mem["contract"],
            sql["contract"],
            cov["contratos_resolvidos"][0] if cov else None,
        ),
        chk("protocolos ligados", mem["protocol"], sql["protocol"], cov.get("protocolos_ligados")),
        chk(
            "protocolos sem vínculo", r.protocols_unlinked, None, cov.get("protocolos_sem_vinculo")
        ),
        chk("protocolos por regra", by_rule, None, cov.get("protocolos_por_regra")),
        chk("contas LMS ligadas", mem["lms_account"], sql["lms_account"], cov.get("contas_lms")),
        chk(
            "PROTOCOL_REF",
            list(r.protocol_ref),
            list(protocol_ref_sql(conn)),
            list(cov["protocol_ref"]) if cov else None,
        ),
        chk(
            "tentativas do LMS",
            mem["lms_quiz_attempt"],
            lms["tentativas"],
            orc(lambda: sum(f.tentativas for f in facts)),
        ),
        chk(
            "simulados abertos sem tentativa",
            None,
            lms["simulados_abertos"],
            orc(lambda: sum(f.simulados_abertos for f in facts)),
        ),
        chk(
            "simulados abertos com tentativa restante",
            None,
            lms["simulados_com_tentativa_restante"],
            orc(lambda: sum(f.simulados_com_tentativa_restante for f in facts)),
        ),
        chk(
            "provas com nota encerradas sem tentativa",
            None,
            lms["encerradas_sem_tentativa"],
            orc(lambda: sum(f.encerradas_sem_tentativa for f in facts)),
        ),
        chk(
            "snapshot do último mês", None, list(r.snapshot), list(cov["snapshot"]) if cov else None
        ),
        chk("logins de analista ausentes", distinct - present, distinct - sql_present),
        chk(
            "logins de analista (distintos)",
            distinct,
            None,
            orc(lambda: len(_analyst_logins(oracle_raw or {}))),
        ),
    ]
    r.checks = [c for c in r.checks if len(c.legs) >= 2]  # sem oráculo, só as de 2 pernas
    return r


def write_golden_login(out: Path, golden: Path, res: Resolution) -> None:
    """`golden_login.json`: caso -> person_key (sem CPF)."""
    cases = json.loads(golden.read_text(encoding="utf-8"))["cases"]
    rows = [
        {"case": c["case"], "person_key": str(res.enrollments[(c["brand"], c["ra"])].person_key)}
        for c in cases
    ]
    (out / "golden_login.json").write_text(json.dumps({"cases": rows}, indent=2), encoding="utf-8")


def write_eval_students(out: Path, raw: RawData, tables: Mapping[str, Sequence[Row]]) -> None:
    """`eval_students.json`: um registro por aluno com os traços usados nas cotas do eval."""
    by: dict[Any, dict[str, list[Row]]] = {}
    for t in ("enrollment", "contract", "agreement", "protocol", "lms_account"):
        for r in tables[t]:
            by.setdefault(r["person_key"], {}).setdefault(t, []).append(r)
    manual = {u["student_lms_id"] for u in raw["lms/mdl_user"] if not u["student_id"].strip()}
    rows = []
    for p in tables["person"]:
        g = by.get(p["person_key"], {})
        enr, contracts = g.get("enrollment", []), g.get("contract", [])
        modalities = {e["modality"] for e in enr}
        payers = {c["payer_type"] for c in contracts}
        rows.append(
            {
                "person_key": str(p["person_key"]),
                "modalidade": modalities.pop() if len(modalities) == 1 else "misto",
                "ativo": any(e["status"] == "ACTIVE" for e in enr),
                "n_matriculas": len(enr),
                "in_collection": any(c["status"] == "IN_COLLECTION" for c in contracts),
                "tem_acordo": bool(g.get("agreement")),
                "protocolo_aberto": any(x["closed_at"] is None for x in g.get("protocol", [])),
                "lms_manual": any(a["lms_user_id"] in manual for a in g.get("lms_account", [])),
                "transferido": any(e["status"] == "TRANSFERRED" or e["origin_brand"] for e in enr),
                "payer_type": next((x for x in ("responsavel", "empresa") if x in payers),
                                   "aluno" if payers else None),
            }
        )  # fmt: skip
    (out / "eval_students.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def load_all(
    raw: RawData,
    settings: EtlSettings,
    *,
    database: str,
    out: Path | None = None,
    eval_out: Path | None = None,
    oracle_raw: dict[str, list[dict[str, str]]] | None = None,
    golden: Path | None = None,
) -> LoadReport:
    key = settings.hmac_key.encode()
    res = resolve(raw, key)
    tables = build_all(raw, key, res=res)
    mem = build_counts(tables)
    with psycopg.connect(
        host=settings.db_host, port=settings.db_port, dbname=database, user="etl_owner",
        password=settings.etl_owner_password, autocommit=False,
    ) as conn:  # fmt: skip
        conn.execute(_sql("util.sql"))
        conn.execute(_sql("schema.sql"))
        _copy(conn, tables)
        sql = sql_counts(conn)
        expected = {t: mem.get(t, 0) for t in sql}
        if sql != expected or set(mem) - set(sql):
            raise RuntimeError(f"contagens SQL != memória: {_diff(sql, mem)}")
        report = _report(
            conn, raw, res, tables, mem, sql, lms_sql_facts(conn, TODAY), key, oracle_raw
        )
        conn.execute(_sql("rls.sql"))
        conn.commit()
    if out is not None:
        write_report(report, out)  # só depois do commit: carga que falha não deixa relatório
    if out is not None and golden is not None:
        write_golden_login(out, golden, res)
    if eval_out is not None:
        write_eval_students(eval_out, raw, tables)
    return report


def _diff(sql: Mapping[str, int], mem: Mapping[str, int]) -> dict[str, tuple[int, int]]:
    return {
        t: (sql.get(t, 0), mem.get(t, 0)) for t in {*sql, *mem} if sql.get(t, 0) != mem.get(t, 0)
    }
