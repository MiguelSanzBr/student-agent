import re

from etl.raw import read_raw
from tests.integration.conftest import Loaded


def _forbidden(raw: dict) -> set[str]:  # type: ignore[type-arg]
    out: set[str] = set()

    def add(v: str, *, digits: bool = False) -> None:
        v = v.strip()
        if len(v) >= 6:
            out.add(v.lower())
        d = re.sub(r"\D", "", v)
        if digits and len(d) >= 10:
            out.add(d)

    for r in raw["sag/person"]:
        add(r["CPF"], digits=True), add(r["EMAIL"]), add(r["PHONE"], digits=True)
        add(r["MOTHER_NAME"])
    for r in raw["finance/customer"]:
        add(r["taxId"], digits=True), add(r["email"]), add(r["phone"], digits=True), add(r["name"])
    for r in raw["finance/company"]:
        add(r["cnpj"], digits=True)
    for r in raw["finance/payment"]:
        add(r["transactionRef"])
    for r in raw["lms/mdl_logstore_standard_log"]:
        add(r["ip"])
    for r in raw["secretary/protocol"]:
        add(r["Requester_CPF"], digits=True), add(r["Requester_Name"])
    for r in raw["secretary/analyst"]:
        add(r["Analyst_Name"])
    for r in raw["lms/mdl_user"]:
        add(r["email"])
        if not r["student_id"].strip() or r["username"].startswith(("prof", "admin", "aluno.")):
            add(f"{r['firstname']} {r['lastname']}")
    # nome de professor só sai pela allowlist da ontologia (class_result.professor): permitido
    allowed = [r["FULL_NAME"].strip().lower() for r in raw["sag/professor"]]
    return {v for v in out if not any(v in a for a in allowed)}


def test_canon_dump_no_pii_by_value(loaded_db: Loaded) -> None:
    raw = read_raw(loaded_db.source)
    forbidden = _forbidden(raw)
    with loaded_db.db.connect("postgres") as c:
        tables = [r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='canon'"
        ).fetchall()]  # fmt: skip
        hits: list[tuple[str, str]] = []
        rx = re.compile("|".join(re.escape(v) for v in sorted(forbidden, key=len, reverse=True)))
        for t in tables:
            for (txt,) in c.execute(f"SELECT lower(x::text) FROM canon.{t} x"):  # type: ignore[call-overload]
                m = rx.search(txt)
                if m:
                    hits.append((t, m.group(0)))  # nunca o valor inteiro
    assert not hits, hits[:10]
    assert len(forbidden) > 100  # varredura de verdade, não vazia
