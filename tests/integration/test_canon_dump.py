import re
import unicodedata
from pathlib import Path

import pytest

from etl.build import load_ontology
from etl.raw import RawData, read_raw
from tests.integration.conftest import Db, Loaded, etl_settings

FULL = Path(__file__).resolve().parents[2] / "data" / "dados.zip"
# colunas de texto livre ou rótulo: onde um CPF/nome vazado apareceria dentro de uma frase
FREE = {
    "texto_livre", "resolution", "reason", "professor", "first_name", "label", "unit_label",
    "name_pt", "subject_name_pt", "ui_label", "codigo_validacao", "delivery",
}  # fmt: skip
CPF_MASKED = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IP = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
DIGITS = re.compile(r"\d{10,}")


def norm(s: str) -> str:
    """Minúsculas e sem acento: 'JOSÉ' e 'jose' casam."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).lower()


def forbidden(raw: RawData) -> tuple[set[str], set[str]]:
    """(valores exatos: CPF/telefone/e-mail/IP/ref/CNPJ; nomes de não-aluno), já normalizados."""
    exact: set[str] = set()
    names: set[str] = set()

    def val(v: str, *, digits: bool = False) -> None:
        v = v.strip()
        if len(v) >= 6:
            exact.add(norm(v))
        d = re.sub(r"\D", "", v)
        if digits and len(d) >= 10:
            exact.add(d)

    def name(v: str) -> None:
        if len(v.strip()) >= 6:
            names.add(norm(v.strip()))

    for r in raw["sag/person"]:
        val(r["CPF"], digits=True), val(r["EMAIL"]), val(r["PHONE"], digits=True)
        name(r["MOTHER_NAME"])
    for r in raw["finance/customer"]:
        val(r["taxId"], digits=True), val(r["email"]), val(r["phone"], digits=True)
        name(r["name"])
    for r in raw["finance/company"]:
        val(r["cnpj"], digits=True)
    for r in raw["finance/payment"]:
        val(r["transactionRef"])
    for r in raw["lms/mdl_logstore_standard_log"]:
        val(r["ip"])
    for r in raw["secretary/protocol"]:
        val(r["Requester_CPF"], digits=True)
        name(r["Requester_Name"])
    for r in raw["secretary/analyst"]:
        name(r["Analyst_Name"])
    for r in raw["lms/mdl_user"]:
        val(r["email"])
        if not r["student_id"].strip() or r["username"].startswith(("prof", "admin", "aluno.")):
            name(f"{r['firstname']} {r['lastname']}")
    return exact, names


def scan(db: Db, raw: RawData) -> list[tuple[str, str]]:
    exact, names = forbidden(raw)
    onto = load_ontology(raw)
    assert len(exact) > 100 and names
    hits: list[tuple[str, str]] = []
    with db.connect("postgres") as c:
        tables = [r[0] for r in c.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='canon'"
        ).fetchall()]  # fmt: skip
        for t in tables:
            for (row,) in c.execute(f"SELECT to_jsonb(x) FROM canon.{t} x"):  # type: ignore[call-overload]
                for col, v in row.items():
                    if not isinstance(v, str):
                        continue
                    if col == "professor" and onto.professor_allowed(v):
                        continue  # só o nome exato da allowlist da ontologia
                    n = norm(v)
                    bad = n in exact or any(m.group(0) in exact for m in DIGITS.finditer(n))
                    bad = (
                        bad
                        or bool(EMAIL.search(n) or IP.search(n))
                        and any(m.group(0) in exact for m in (*EMAIL.finditer(n), *IP.finditer(n)))
                    )
                    if col in FREE:
                        bad = bad or bool(CPF_MASKED.search(n)) or any(x in n for x in names)
                    if bad:
                        hits.append((t, col))  # nunca o valor
    return hits


def test_canon_dump_no_pii_by_value(loaded_db: Loaded) -> None:
    assert not scan(loaded_db.db, read_raw(loaded_db.source))


def test_scan_detects_planted_pii(loaded_db: Loaded) -> None:
    """Controle positivo: o mesmo scanner acusa um CPF e um nome plantados."""
    raw = read_raw(loaded_db.source)
    cpf = re.sub(r"\D", "", raw["sag/person"][0]["CPF"])
    nome = raw["secretary/analyst"][0]["Analyst_Name"]
    with loaded_db.db.connect("postgres") as c:
        c.execute(
            "INSERT INTO canon.protocol_message (person_key, texto_livre) "
            "VALUES (gen_random_uuid(), %s), (gen_random_uuid(), %s)",
            (f"cpf {cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}", nome.upper()),
        )
    assert len(scan(loaded_db.db, raw)) >= 2


@pytest.mark.fulldata
def test_canon_dump_no_pii_by_value_full(db: Db, tmp_path: Path) -> None:
    if not FULL.exists():
        pytest.skip("data/dados.zip ausente")
    from etl.load import load_all

    raw = read_raw(FULL)
    load_all(raw, etl_settings(db), database=db.name)
    assert not scan(db, raw)
