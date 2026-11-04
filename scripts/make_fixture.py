"""Gera a fixture mínima (tests/fixtures/mini + golden.json) a partir de `dados.zip`.

Uso: uv run python -I scripts/make_fixture.py data/dados.zip tests/fixtures/mini
Os CPFs do dataset nunca vão para o git: cada um vira um CPF sintético válido (`fake_cpf`), igual em
todos os arquivos, no formato de cada coluna. Os casos golden são (marca, RA), sem CPF.
"""

import csv
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # -I não põe o repo no path

from core.normalize import digits, lms_login, normalize_academic_ref, normalize_cpf  # noqa: E402
from etl.raw import RawData, read_raw  # noqa: E402

Row = Mapping[str, str]
Key = tuple[str, str]

# (caso, marca, RA, outras matrículas da mesma pessoa que o caso exige)
GOLDEN: list[tuple[int, str, str, list[Key]]] = [
    (1, "UniSaoPaulo", "181722", []),
    (2, "UniAtlantico_EAD", "012815", []),
    (3, "UniSaoPaulo_EAD", "014214", [("UniSaoPaulo", "181544")]),
    (4, "UniSaoPaulo", "180823", []),
    (5, "UniSaoPaulo_EAD", "012157", []),
    (6, "UniSaoPaulo_EAD", "013226", []),
    (7, "UniSaoPaulo_EAD", "011703", []),
    (8, "UniSaoPaulo", "182230", []),
    (9, "UniSaoPaulo", "182915", [("UniSaoPaulo_EAD", "011843")]),
    (10, "UniSaoPaulo_EAD", "011932", []),
]
SIBLING: Key = ("UniSaoPaulo", "180548")  # irmão do golden 4
LOCKED: Key = ("UniAraucaria", "090948")  # matrícula LOCKED escolhida à mão
THIRD_PARTY = "20230002046"  # protocolo de terceiro escolhido à mão
TENANT = {"UniSaoPaulo": "1", "UniMontanhas": "2", "UniLitoral": "3", "UniAtlantico": "4",
          "UniAraucaria": "5"}  # fmt: skip
CATALOGS = [
    "sag/brand", "sag/unit", "sag/course", "sag/curriculum", "sag/curriculum_subject",
    "sag/subject", "sag/course_offer", "sag/term", "sag/professor", "secretary/demand_type",
    "secretary/unit", "secretary/analyst", "finance/company", "finance/product",
    "finance/price_list", "lms/mdl_tenant", "lms/mdl_course_categories", "lms/mdl_user_info_field",
]  # fmt: skip


_CPF_RX = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}|\b\d{8,11}\b")


def _dv(base: str) -> str:
    for n in (9, 10):
        base += str(
            sum(int(c) * w for c, w in zip(base, range(n + 1, 1, -1), strict=True)) * 10 % 11 % 10
        )
    return base[9:]


def fake_cpf(real: str) -> str:
    """CPF sintético válido, determinístico por CPF real; mantém pontuação e zeros à esquerda."""
    d = digits(real).zfill(11)
    for salt in range(100):  # repete só se der dígitos repetidos (inválido) ou o próprio real
        n = (
            int(hashlib.sha256(f"student-agent-fixture:{salt}:{d}".encode()).hexdigest(), 16)
            % 10**9
        )
        base = f"{n:09d}"
        if len(set(base)) > 1 and base + _dv(base) != d:
            break
    out = base + _dv(base)
    if "." in real:
        return f"{out[:3]}.{out[3:6]}.{out[6:9]}-{out[9:]}"
    return out if len(real) >= 11 else out.lstrip("0")


def real_cpfs(raw: RawData) -> set[str]:
    return {
        d
        for rows in raw.values()
        for r in rows
        for k, v in r.items()
        if ("cpf" in k.lower() or "taxid" in k.lower()) and len(d := digits(v)) == 11
    }


def pseudonymize(cell: str, real: set[str]) -> str:
    return _CPF_RX.sub(lambda m: fake_cpf(m[0]) if digits(m[0]).zfill(11) in real else m[0], cell)


def ek(r: Row) -> Key:
    return r["BRAND"], r["STUDENT_ID"]


def pick(rows: Sequence[Row], pred: Callable[[Row], bool]) -> list[Row]:
    return [r for r in rows if pred(r)]


def ref_digits(v: str) -> str:
    return digits(v)


def select(raw: RawData) -> dict[str, list[Row]]:
    enr = raw["sag/enrollment"]
    by_key = {ek(r): r for r in enr}
    person = {r["PERSON_ID"]: r for r in raw["sag/person"]}
    gold = {(b, ra) for _, b, ra, _ in GOLDEN} | {k for *_, o in GOLDEN for k in o}
    ras = {ra for _, ra in gold}
    partners = {ek(r) for r in enr if r["STUDENT_ID"] in ras} - gold
    seed = gold | partners | {SIBLING, LOCKED}
    seed |= {  # uma volta de origem (transferência) para os que já estão
        (by_key[k]["ORIGIN_BRAND"], by_key[k]["ORIGIN_STUDENT_ID"])
        for k in seed
        if by_key[k]["ORIGIN_BRAND"]
    }
    out: dict[str, list[Row]] = {t: list(raw[t]) for t in CATALOGS}
    keep = lambda r: ek(r) in seed  # noqa: E731
    out["sag/enrollment"] = pick(enr, keep)
    pids = {r["PERSON_ID"] for r in out["sag/enrollment"]}
    out["sag/person"] = pick(raw["sag/person"], lambda r: r["PERSON_ID"] in pids)
    for t in ("enrollment_status_history", "term_enrollment", "student_class"):
        out[f"sag/{t}"] = pick(raw[f"sag/{t}"], keep)
    classes = {r["CLASS_ID"] for r in out["sag/student_class"]}
    out["sag/class_section"] = pick(raw["sag/class_section"], lambda r: r["CLASS_ID"] in classes)

    # financeiro: contrato -> matrícula pelo academicRef normalizado + marca (nunca por taxId)
    brand_of = {c["companyCode"]: c["tradeName"] for c in raw["finance/company"]}
    brands = {b.upper(): b for b, _ in by_key}

    def contract_key(c: Row) -> Key | None:
        ra = normalize_academic_ref(c["academicRef"])
        base = brands.get(brand_of[c["companyCode"]])
        if ra is None or base is None:
            return None
        return (base + "_EAD" if c["businessArea"] == "DIGITAL" else base), ra

    out["finance/contract"] = pick(raw["finance/contract"], lambda c: contract_key(c) in seed)
    cids = {c["contractId"] for c in out["finance/contract"]}
    custs = {c["customerId"] for c in out["finance/contract"]}
    out["finance/customer"] = pick(raw["finance/customer"], lambda r: r["customerId"] in custs)
    for t in ("invoice", "discount", "receivable_snapshot", "agreement"):
        out[f"finance/{t}"] = pick(raw[f"finance/{t}"], lambda r: r["contractId"] in cids)
    iids = {r["invoiceId"] for r in out["finance/invoice"]}
    for t in ("invoice_tax", "payment", "late_charge"):
        out[f"finance/{t}"] = pick(raw[f"finance/{t}"], lambda r: r["invoiceId"] in iids)
    aids = {r["agreementId"] for r in out["finance/agreement"]}
    out["finance/agreement_invoice"] = pick(
        raw["finance/agreement_invoice"], lambda r: r["agreementId"] in aids
    )

    # secretaria: RA (pad 6, qualquer marca), CPF do aluno/pagador, referências cruzadas, terceiro
    cpfs = {digits(person[p]["CPF"]) for p in pids}
    cpfs |= {c for r in out["finance/customer"] if (c := normalize_cpf(r["taxId"])) is not None}
    refs = {ref_digits(r["PROTOCOL_REF"]) for r in out["sag/enrollment_status_history"]}
    refs |= {r["protocolRef"] for r in (*out["finance/invoice"], *out["finance/agreement"])}
    refs |= {THIRD_PARTY}
    refs.discard("")

    def proto(r: Row) -> bool:
        return (
            r["Stu_ID"].zfill(6) in {k[1] for k in seed}
            and r["Stu_ID"] != ""
            or (normalize_cpf(r["Requester_CPF"]) or "-") in cpfs
            or r["Protocol"] in refs
        )

    out["secretary/protocol"] = [
        r for r in raw["secretary/protocol"] if proto(r) or r["Protocol"] == THIRD_PARTY
    ]
    pr = {r["Protocol"] for r in out["secretary/protocol"]}
    for t in ("protocol_status_history", "protocol_interaction", "issued_document",
              "satisfaction_survey"):  # fmt: skip
        out[f"secretary/{t}"] = pick(raw[f"secretary/{t}"], lambda r: r["Protocol"] in pr)

    # LMS: conta por tenant + login derivado do RA; contas manuais pelo log de login
    want: set[tuple[str, str]] = set()  # (login minúsculo, tenant); RAs EAD colidem entre marcas
    for b, ra in seed:
        base = b.removesuffix("_EAD")
        want.add((("ead_" if b.endswith("_EAD") else "pres_") + ra[:5], TENANT[base]))
    users = raw["lms/mdl_user"]
    ids = {
        u["student_lms_id"]
        for u in users
        if (lg := lms_login(u["student_id"])) and (lg.lower(), u["tenant_id"]) in want
    }
    tenant_of = {u["student_lms_id"]: u["tenant_id"] for u in users}
    for r in raw["lms/mdl_logstore_standard_log"]:
        if r["eventname"].endswith("user_loggedin") and r["other"].startswith('{"username"'):
            u = json.loads(r["other"])["username"].strip().lower()
            if (u, tenant_of.get(r["userid"], "")) in want:
                ids.add(r["userid"])
    noise = [  # contas que o ETL descarta: admin, integração, suporte, teste, professor
        next(u for u in users if u["username"].lower().startswith(name))
        for name in ("admin", "integration.sag", "support.lms", "aluno.teste", "prof")
    ]
    ids |= {u["student_lms_id"] for u in noise}
    out["lms/mdl_user"] = pick(users, lambda r: r["student_lms_id"] in ids)
    out["lms/mdl_user_info_data"] = pick(
        raw["lms/mdl_user_info_data"], lambda r: r["userid"] in ids
    )
    out["lms/mdl_user_enrolments"] = pick(
        raw["lms/mdl_user_enrolments"], lambda r: r["userid"] in ids
    )
    courses = {r["courseid"] for r in out["lms/mdl_user_enrolments"]}
    out["lms/mdl_course"] = pick(raw["lms/mdl_course"], lambda r: r["id"] in courses)
    out["lms/mdl_course_modules"] = pick(
        raw["lms/mdl_course_modules"], lambda r: r["course"] in courses
    )
    out["lms/mdl_quiz"] = pick(raw["lms/mdl_quiz"], lambda r: r["course"] in courses)
    out["lms/mdl_quiz_attempts"] = pick(raw["lms/mdl_quiz_attempts"], lambda r: r["userid"] in ids)
    out["lms/mdl_logstore_standard_log"] = pick(
        raw["lms/mdl_logstore_standard_log"],
        lambda r: r["userid"] in ids or r["relateduserid"] in ids,
    )
    return out


def write(dest: Path, raw: RawData, sel: Mapping[str, Iterable[Row]]) -> None:
    real = real_cpfs(raw)
    fakes = {fake_cpf(c) for c in real}
    assert len(fakes) == len(real) and not {digits(f) for f in fakes} & real  # mapa injetivo
    for name in raw:
        rows = [{k: pseudonymize(v, real) for k, v in r.items()} for r in sel[name]]
        assert rows, f"tabela vazia na fixture: {name}"
        path = dest / f"{name}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)


def main(src: Path, dest: Path) -> None:
    raw = read_raw(src)
    cpf = {r["PERSON_ID"]: digits(r["CPF"]) for r in raw["sag/person"]}
    enr = {(r["BRAND"], r["STUDENT_ID"]): r for r in raw["sag/enrollment"]}
    for _, b, ra, others in GOLDEN:  # as outras matrículas do caso são da mesma pessoa (mesmo CPF)
        assert all(cpf[enr[k]["PERSON_ID"]] == cpf[enr[(b, ra)]["PERSON_ID"]] for k in others), (
            b,
            ra,
        )
    sel = select(raw)
    write(dest, raw, sel)
    cases = [{"case": n, "brand": b, "ra": ra, "other_enrollments": [list(k) for k in o]}
             for n, b, ra, o in GOLDEN]  # fmt: skip
    gj = dest.parent / "golden.json"
    gj.write_text(json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
