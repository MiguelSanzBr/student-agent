"""Oráculo independente: gabaritos calculados direto dos CSV crus do case.

Só biblioteca padrão. Nada de core/, etl/ nem app/: a independência é o ponto, para que as
respostas do agente e os números congelados do ETL possam ser conferidos contra um cálculo
que não compartilha código com eles.
"""

from __future__ import annotations

import csv
import io
import json
import random
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

Raw = dict[str, list[dict[str, str]]]
Enr = tuple[str, str]  # (BRAND, STUDENT_ID)

# Data de referência do case (31/10/2026). Fixa aqui: o oráculo não importa core/clock.
REFERENCE = date(2026, 10, 31)
BRT = timezone(timedelta(hours=-3))  # America/Sao_Paulo (sem horário de verão desde 2019)


# ----------------------------------------------------------------------------- leitura
def load_csv(source: Path) -> dict[str, list[dict[str, str]]]:
    """Lê `dados.zip` (ou uma pasta já extraída) e devolve {"sag/enrollment": linhas, ...}."""
    source = Path(source)
    raw: Raw = {}

    def read(name: str, fh: Any) -> None:
        text = io.TextIOWrapper(fh, encoding="utf-8", newline="")
        raw[name] = list(csv.DictReader(text))

    if source.is_dir():
        for p in sorted(source.rglob("*.csv")):
            with p.open("rb") as fh:
                read(p.relative_to(source).with_suffix("").as_posix(), fh)
    else:
        with zipfile.ZipFile(source) as z:
            for n in sorted(z.namelist()):
                if n.endswith(".csv"):
                    with z.open(n) as zfh:
                        read(n[: -len(".csv")], zfh)
    return raw


# ----------------------------------------------------------------------------- regras básicas
def ra_check_digit(base5: str) -> int:
    """DV do RA: pesos 6,5,4,3,2 sobre os 5 dígitos; 11 - (soma mod 11); 10 ou 11 vira 0."""
    s = sum(int(d) * w for d, w in zip(base5, (6, 5, 4, 3, 2), strict=True))
    dv = 11 - s % 11
    return 0 if dv >= 10 else dv


def ra_valid(ra: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", ra)) and ra_check_digit(ra[:5]) == int(ra[5])


def normalize_academic_ref(ref: str) -> str:
    """`academicRef` do financeiro -> RA de 6 dígitos (sem espaços e sem `-`; `dddd-d` com zero
    à esquerda; 5 dígitos sem traço é só a base e recebe o DV)."""
    s = ref.strip().replace(" ", "")
    if "-" in s:
        return s.replace("-", "").zfill(6)
    if len(s) == 5:
        return s + str(ra_check_digit(s))
    return s


def lpad(value: str, n: int) -> str:
    return value.strip().zfill(n)


def digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def cpf11(value: str) -> str:
    return lpad(digits(value), 11)


def protocol_ref_digits(ref: str) -> str:
    """`PRT-2022/0003006` -> `20220003006`."""
    return digits(ref)


def _ascii(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _ascii(s).lower())


def money(cents: int) -> str:
    """Centavos -> `R$ 1.234,56`."""
    sign = "-" if cents < 0 else ""
    reais, c = divmod(abs(cents), 100)
    return f"{sign}R$ {reais:,}".replace(",", ".") + f",{c:02d}"


def fmt_decimal1(value: str) -> str:
    """Decimal do CSV -> 1 casa, arredondando meio para cima, com vírgula (`36.55` -> `36,6`)."""
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")


def br_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _epoch(ts: str) -> int:
    return int(ts or 0)


def _brt_date(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, tz=BRT).date()


def _iso_date(ts: str) -> date:
    """`2022-03-04T13:57:29Z` / `2026-11-01 02:01:01+00:00` -> data em Brasília."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(BRT).date()


# ----------------------------------------------------------------------------- índices
_MEMO: dict[int, tuple[Raw, dict[str, Any]]] = {}


def _cached[F: Callable[..., Any]](fn: F) -> F:
    """Memoiza por `raw` (identidade): os índices custam segundos no CSV completo."""

    def wrapper(raw: Raw) -> Any:
        hit = _MEMO.get(id(raw))
        if hit is None or hit[0] is not raw:
            hit = _MEMO[id(raw)] = (raw, {})
        if fn.__name__ not in hit[1]:
            hit[1][fn.__name__] = fn(raw)
        return hit[1][fn.__name__]

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper  # type: ignore[return-value]


COURSE_SYNONYMS = {
    "ADM": ("Administration", "Business Admin.", "Business Administration"),
    "ADS": ("ADS", "Systems Dev.", "Systems Analysis & Development"),
    "CON": ("Acct.", "Accounting", "Accounting Sciences"),
    "DIR": ("Law", "Law School", "Legal Studies"),
    "ENF": ("Nursing", "Nurse Training", "Nursing Sciences"),
    "PSI": ("Psych.", "Psychology", "Psychology Dept."),
    "ENC": ("Civil Eng.", "Civil Engineering", "Engineering - Civil"),
    "PED": ("Education - Pedagogy", "Pedagogy", "Pedagogy (DL)"),
}
LABEL_TO_ACRONYM = {_slug(v): k for k, vs in COURSE_SYNONYMS.items() for v in vs}


@_cached
def person_cpf(raw: Raw) -> dict[str, str]:
    return {p["PERSON_ID"]: cpf11(p["CPF"]) for p in raw["sag/person"]}


@_cached
def enrollments(raw: Raw) -> dict[Enr, dict[str, str]]:
    return {(e["BRAND"], e["STUDENT_ID"]): e for e in raw["sag/enrollment"]}


@_cached
def course_acronym(raw: Raw) -> dict[str, str]:
    return {c["COURSE"]: c["COURSE_ACRONYM"] for c in raw["sag/course"]}


@_cached
def secretary_unit_brand(raw: Raw) -> dict[str, str]:
    """Unit_Code da secretaria -> BRAND do SAG, por marca (nome normalizado) + sufixo `-EAD`."""
    sag_brands = {_slug(b["BRAND"]): b["BRAND"] for b in raw["sag/brand"]}
    out = {}
    for u in raw["secretary/unit"]:
        fam = sag_brands[_slug(u["Brand_Name"])]
        out[u["Unit_Code"]] = fam + "_EAD" if u["Unit_Code"].endswith("-EAD") else fam
    return out


@_cached
def contract_enrollment(raw: Raw) -> dict[str, Enr]:
    """contractId -> (BRAND, RA): `companyCode` = marca, `businessArea` = modalidade e
    `academicRef` normalizado = RA. Nunca por taxId nem por customerId."""
    comp = {c["companyCode"]: c["tradeName"] for c in raw["finance/company"]}
    fam = {
        _slug(b["BRAND"]): b["BRAND"] for b in raw["sag/brand"] if not b["BRAND"].endswith("_EAD")
    }
    out = {}
    for c in raw["finance/contract"]:
        brand = fam[_slug(comp[c["companyCode"]])]
        if c["businessArea"] == "DIGITAL":
            brand += "_EAD"
        out[c["contractId"]] = (brand, normalize_academic_ref(c["academicRef"]))
    return out


@_cached
def payers_by_enrollment(raw: Raw) -> dict[Enr, set[str]]:
    """Enrollment -> conjunto de `taxId` (só dígitos) dos pagadores dos seus contratos."""
    tax = {c["customerId"]: digits(c["taxId"]) for c in raw["finance/customer"]}
    out: dict[Enr, set[str]] = defaultdict(set)
    ce = contract_enrollment(raw)
    for c in raw["finance/contract"]:
        out[ce[c["contractId"]]].add(tax[c["customerId"]])
    return out


# ----------------------------------------------------------------------------- protocolos
@dataclass(frozen=True)
class LinkResult:
    enrollment: tuple[str, str] | None
    rule: str | None


@_cached
def oracle_link_protocols(raw: Raw) -> dict[str, LinkResult]:
    enr = enrollments(raw)
    pcpf = person_cpf(raw)
    acr = course_acronym(raw)
    ubrand = secretary_unit_brand(raw)
    payers = payers_by_enrollment(raw)
    by_brand_cpf: dict[tuple[str, str], list[Enr]] = defaultdict(list)
    for k, e in enr.items():
        by_brand_cpf[(k[0], pcpf[e["PERSON_ID"]])].append(k)
    payer_enrs: dict[tuple[str, str], list[Enr]] = defaultdict(list)
    for k, taxes in payers.items():
        for t in taxes:
            payer_enrs[(k[0], t)].append(k)

    out: dict[str, LinkResult] = {}
    for p in raw["secretary/protocol"]:
        brand = ubrand[p["Unit_Code"]]
        cpf = cpf11(p["Requester_CPF"])
        label = LABEL_TO_ACRONYM.get(_slug(p["Course_Label"]))
        res = LinkResult(None, None)
        key = (brand, lpad(p["Stu_ID"], 6)) if p["Stu_ID"].strip() else None
        if key in enr:
            if pcpf[enr[key]["PERSON_ID"]] == cpf:
                res = LinkResult(key, "ra_cpf")
            elif cpf in payers[key]:
                res = LinkResult(key, "guardian_payer")
        if res.rule is None:
            own = by_brand_cpf.get((brand, cpf), [])
            if len(own) == 1:
                res = LinkResult(own[0], "cpf_brand_unique")
            elif len(own) > 1:
                match = [k for k in own if acr[enr[k]["COURSE"]] == label]
                if len(match) == 1:
                    res = LinkResult(match[0], "cpf_brand_by_date")
            elif key is None:  # com Stu_ID digitado errado não liga: precisão acima de cobertura
                paid = payer_enrs.get((brand, cpf), [])
                if len(paid) == 1 and acr[enr[paid[0]]["COURSE"]] == label:
                    res = LinkResult(paid[0], "guardian_payer_unique")
        out[p["Protocol"]] = res
    return out


# ----------------------------------------------------------------------------- contas do LMS
_SSO = re.compile(r"^(PRES|EAD)_(\d{5})$")
_NOT_STUDENT = re.compile(r"^(admin|integration\.sag|support\.lms|prof\d+|aluno\.teste\d+)$")


def _lms_login_to_enr(raw: Raw, tenant: str, login: str) -> Enr | None:
    m = _SSO.match(login.strip().upper())
    if not m:
        return None
    tenants = {t["id"]: _slug(t["slug"]) for t in raw["lms/mdl_tenant"]}
    brand = next(b["BRAND"] for b in raw["sag/brand"] if _slug(b["BRAND"]) == tenants[tenant])
    if m.group(1) == "EAD":
        brand += "_EAD"
    key = (brand, m.group(2) + str(ra_check_digit(m.group(2))))
    return key if key in enrollments(raw) else None


@_cached
def oracle_lms_accounts(raw: Raw) -> dict[int, tuple[str, str]]:
    """student_lms_id -> matrícula. SSO por tenant + `student_id`; contas manuais de aluno pelo
    `username` dos eventos `user_loggedin` do próprio usuário. Teste, professor, admin e
    integração ficam de fora (com asserção)."""
    out: dict[int, tuple[str, str]] = {}
    logins: dict[str, set[str]] = defaultdict(set)
    for ev in raw["lms/mdl_logstore_standard_log"]:
        if ev["eventname"].endswith("user_loggedin"):
            other = json.loads(ev["other"] or "null")
            if isinstance(other, dict) and other.get("username"):
                logins[ev["userid"]].add(str(other["username"]))
    for u in raw["lms/mdl_user"]:
        uid = int(u["student_lms_id"])
        if _NOT_STUDENT.match(u["username"].strip().lower()) or u[
            "student_id"
        ].strip().upper().startswith("PROF_"):
            assert uid not in out
            continue
        if u["auth"] == "manual":
            cands = {
                e
                for name in logins.get(u["student_lms_id"], ())
                if (e := _lms_login_to_enr(raw, u["tenant_id"], name))
            }
            if len(cands) == 1:
                out[uid] = next(iter(cands))
        else:
            e = _lms_login_to_enr(raw, u["tenant_id"], u["student_id"])
            if e:
                out[uid] = e
    assert not any(
        _NOT_STUDENT.match(u["username"].lower())
        for u in raw["lms/mdl_user"]
        if int(u["student_lms_id"]) in out
    )
    return out


# ----------------------------------------------------------------------------- financeiro
@dataclass(frozen=True)
class FinanceFacts:
    total_para_quitar: int  # principal vencido + encargos em aberto das faturas vencidas
    principal_vencido: int
    encargos: int
    faturas_vencidas: int
    proximo_vencimento: date | None  # próxima fatura aberta ainda a vencer
    proximo_valor: int | None  # saldo dessa fatura
    saldo: dict[str, int] = field(default_factory=dict)  # invoiceId -> saldo (OPEN/OVERDUE/PARTIAL)
    para_quitar: dict[str, int] = field(default_factory=dict)  # invoiceId -> saldo + encargos
    pagadores: tuple[str, ...] = ()  # "aluno" | "responsável" | "empresa"
    contratos: tuple[str, ...] = ()


@_cached
def _finance_index(raw: Raw) -> dict[str, Any]:
    """Por fatura: pagamentos CONFIRMED e encargos não dispensados; por contrato: faturas."""
    paid: dict[str, list[dict[str, str]]] = defaultdict(list)
    for p in raw["finance/payment"]:
        if p["status"] == "CONFIRMED":  # REVERSED não conta
            paid[p["invoiceId"]].append(p)
    charges: dict[str, list[dict[str, str]]] = defaultdict(list)
    for lc in raw["finance/late_charge"]:
        if lc["waived"] != "True":
            charges[lc["invoiceId"]].append(lc)
    by_contract: dict[str, list[dict[str, str]]] = defaultdict(list)
    for i in raw["finance/invoice"]:
        by_contract[i["contractId"]].append(i)
    return {"paid": paid, "charges": charges, "by_contract": by_contract}


def invoice_balance(raw: Raw, invoice: Mapping[str, str]) -> int:
    paid = _finance_index(raw)["paid"][invoice["invoiceId"]]
    return int(invoice["netAmountCents"]) - sum(int(p["principalCents"]) for p in paid)


def invoice_open_charges(raw: Raw, invoice: Mapping[str, str]) -> int:
    ix = _finance_index(raw)
    iid = invoice["invoiceId"]
    due = sum(int(c["fineCents"]) + int(c["interestCents"]) for c in ix["charges"][iid])
    got = sum(int(p["fineCents"]) + int(p["interestCents"]) for p in ix["paid"][iid])
    return due - got


_OPEN_STATUS = (
    "OPEN",
    "OVERDUE",
    "PARTIAL",
)  # RENEGOTIATED sai: as parcelas do acordo a substituem


def _payer_kind(raw: Raw, contract: Mapping[str, str], student_cpf: str) -> str:
    cust = next(c for c in raw["finance/customer"] if c["customerId"] == contract["customerId"])
    if cust["customerType"] != "INDIVIDUAL":
        return "empresa"
    return "aluno" if cpf11(cust["taxId"]) == student_cpf else "responsável"


def oracle_financial(raw: Raw, enr: Enr, today: date = REFERENCE) -> FinanceFacts:
    ix = _finance_index(raw)
    ce = contract_enrollment(raw)
    contracts = [c for c in raw["finance/contract"] if ce[c["contractId"]] == enr]
    scpf = person_cpf(raw)[enrollments(raw)[enr]["PERSON_ID"]]
    saldo: dict[str, int] = {}
    quitar: dict[str, int] = {}
    principal = encargos = vencidas = 0
    upcoming: list[tuple[str, str, int]] = []
    for c in contracts:
        for i in ix["by_contract"][c["contractId"]]:
            if i["status"] not in _OPEN_STATUS:
                continue
            bal, chg = invoice_balance(raw, i), invoice_open_charges(raw, i)
            saldo[i["invoiceId"]] = bal
            quitar[i["invoiceId"]] = bal + chg
            if date.fromisoformat(i["dueDate"]) < today:
                principal += bal
                encargos += chg
                vencidas += 1
            else:
                upcoming.append((i["dueDate"], i["invoiceId"], bal))
    nxt = min(upcoming) if upcoming else None
    return FinanceFacts(
        total_para_quitar=principal + encargos,
        principal_vencido=principal,
        encargos=encargos,
        faturas_vencidas=vencidas,
        proximo_vencimento=date.fromisoformat(nxt[0]) if nxt else None,
        proximo_valor=nxt[2] if nxt else None,
        saldo=saldo,
        para_quitar=quitar,
        pagadores=tuple(sorted({_payer_kind(raw, c, scpf) for c in contracts})),
        contratos=tuple(sorted(c["contractId"] for c in contracts)),
    )


def oracle_snapshot_check(raw: Raw) -> tuple[int, int]:
    """Gabarito externo: (contratos do último snapshot que batem com o cálculo pelas faturas,
    contratos no último snapshot). Compara nº de faturas vencidas, principal, multa e juros."""
    ix = _finance_index(raw)
    last = max(s["snapshotDate"] for s in raw["finance/receivable_snapshot"])
    ref = date.fromisoformat(last)
    mine: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for i in raw["finance/invoice"]:
        if i["status"] in ("OVERDUE", "PARTIAL") and date.fromisoformat(i["dueDate"]) < ref:
            iid = i["invoiceId"]
            paid = ix["paid"][iid]
            a = mine[i["contractId"]]
            a[0] += 1
            a[1] += invoice_balance(raw, i)
            a[2] += sum(int(c["fineCents"]) for c in ix["charges"][iid]) - sum(
                int(p["fineCents"]) for p in paid
            )
            a[3] += sum(int(c["interestCents"]) for c in ix["charges"][iid]) - sum(
                int(p["interestCents"]) for p in paid
            )
    rows = [s for s in raw["finance/receivable_snapshot"] if s["snapshotDate"] == last]
    ok = sum(
        1
        for s in rows
        if mine.get(s["contractId"])
        == [
            int(s["overdueInvoices"]),
            int(s["principalCents"]),
            int(s["fineCents"]),
            int(s["interestCents"]),
        ]
    )
    return ok, len(rows)


# ----------------------------------------------------------------------------- acadêmico
@dataclass(frozen=True)
class CoursesFacts:
    status: str
    status_date: str
    curso: str
    semestre_nivel: int
    conclusao_pct: str  # texto cru do SAG; formate com fmt_decimal1
    cr: str | None
    disciplinas: list[dict[str, str]]


def _term_key(term: str) -> int:
    y, s = term.split("/")
    return int(y) * 2 + int(s) - 1


def _term_add(term: str, n: int) -> str:
    k = _term_key(term) + n
    return f"{k // 2}/{k % 2 + 1}"


def current_term(raw: Raw) -> str:
    return next(t["TERM"] for t in raw["sag/term"] if t["IS_CURRENT"] == "Y")


def oracle_courses(raw: Raw, enr: Enr) -> CoursesFacts:
    e = enrollments(raw)[enr]
    sections = {c["CLASS_ID"]: c for c in raw["sag/class_section"]}
    subjects = {s["SUBJECT_CODE"]: s["SUBJECT_NAME"] for s in raw["sag/subject"]}
    rows = []
    for sc in raw["sag/student_class"]:
        if (sc["BRAND"], sc["STUDENT_ID"]) != enr:
            continue
        sec = sections[sc["CLASS_ID"]]
        rows.append(
            {
                "disciplina": sec["SUBJECT_CODE"],
                "nome": subjects[sec["SUBJECT_CODE"]],
                "semestre": sec["TERM"],
                "av1": sc["GRADE_AV1"],
                "av2": sc["GRADE_AV2"],
                "av3": sc["GRADE_AV3"],
                "final": sc["FINAL_GRADE"],  # nota armazenada; nunca recalculada
                "frequencia": sc["ATTENDANCE_PCT"],
                "resultado": sc["RESULT"],
            }
        )
    rows.sort(key=lambda r: (r["semestre"], r["disciplina"]))
    return CoursesFacts(
        status=e["ENROLLMENT_STATUS"],
        status_date=e["STATUS_DATE"],
        curso=course_acronym(raw)[e["COURSE"]],
        semestre_nivel=int(e["SEMESTER"]),
        conclusao_pct=e["CURRENT_CURRICULUM_COMPLETION"],
        cr=e["GPA"] or None,
        disciplinas=rows,
    )


@dataclass(frozen=True)
class Forecast:
    previsao: str | None  # semestre de conclusão "AAAA/S", "se cursar regularmente"
    duracao: int
    semestre_nivel: int


def oracle_forecast(raw: Raw, enr: Enr) -> Forecast:
    """Previsão se cursar regularmente: cada semestre avança 1 nível; a matrícula no nível N de
    D conclui (D - N) semestres depois do corrente. Só para ACTIVE."""
    e = enrollments(raw)[enr]
    dur = int(
        next(c for c in raw["sag/course"] if c["COURSE"] == e["COURSE"])["DURATION_SEMESTERS"]
    )
    lvl = int(e["SEMESTER"])
    prev = (
        _term_add(current_term(raw), max(0, dur - lvl))
        if e["ENROLLMENT_STATUS"] == "ACTIVE"
        else None
    )
    return Forecast(prev, dur, lvl)


# ----------------------------------------------------------------------------- protocolos
STATUS_PT = {
    "Open": "aberto",
    "In Analysis": "em análise",
    "Pending Requester": "aguardando você",
    "Resolved": "resolvido",
    "Cancelled": "cancelado",
    "Reopened": "reaberto",
}


def oracle_protocols(raw: Raw, enr: Enr) -> list[dict[str, str]]:
    links = oracle_link_protocols(raw)
    sla = {d["Demand_Code"]: int(d["SLA_Hours"]) for d in raw["secretary/demand_type"]}
    role = {
        "Student": "aluno",
        "Alumni": "aluno",
        "Guardian": "responsável",
        "Third Party": "terceiro",
    }
    out = []
    for p in raw["secretary/protocol"]:
        if links[p["Protocol"]].enrollment != enr:
            continue
        opened = datetime.fromisoformat(p["Opened_At"].replace("Z", "+00:00"))
        due = (opened + timedelta(hours=sla[p["Demand"]])).astimezone(BRT).date()
        out.append(
            {
                "protocolo": p["Protocol"],
                "servico": p["Demand"],
                "status": STATUS_PT[p["Demand_Status"]],
                "aberto_em": br_date(opened.astimezone(BRT).date()),
                "prazo": br_date(due),
                "solicitante": role[p["Requester_Type"]],
                "regra": links[p["Protocol"]].rule or "",
            }
        )
    out.sort(key=lambda r: r["protocolo"])
    return out


def oracle_enrollment_events(raw: Raw, enr: Enr) -> list[dict[str, str]]:
    return [
        {
            "data": br_date(date.fromisoformat(h["EVENT_DATE"])),
            "de": h["OLD_STATUS"],
            "para": h["NEW_STATUS"],
            "motivo": h["REASON"],
            "semestre": h["TERM"],
            "protocol_ref": protocol_ref_digits(h["PROTOCOL_REF"]),
        }
        for h in sorted(raw["sag/enrollment_status_history"], key=lambda r: int(r["HISTORY_ID"]))
        if (h["BRAND"], h["STUDENT_ID"]) == enr
    ]


def oracle_term_registration(raw: Raw, enr: Enr) -> list[dict[str, str]]:
    return [
        {"semestre": t["TERM"], "status": t["TERM_STATUS"], "data": t["REGISTRATION_DATE"]}
        for t in sorted(raw["sag/term_enrollment"], key=lambda r: _term_key(r["TERM"]))
        if (t["BRAND"], t["STUDENT_ID"]) == enr
    ]


# ----------------------------------------------------------------------------- LMS
@dataclass(frozen=True)
class LmsFacts:
    simulados_abertos: int  # janela aberta e sem tentativa
    simulados_com_tentativa_restante: int  # janela aberta e tentativas < permitidas
    proxima_avaliacao: str | None  # dd/mm/aaaa
    fonte: str | None  # "lms" | "calendario"
    encerradas_sem_tentativa: int  # provas que valem nota (AV) encerradas sem tentativa
    tentativas: int
    proxima_tipo: str | None = None
    tem_conta: bool = True


@_cached
def _lms_index(raw: Raw) -> dict[str, Any]:
    quizzes: dict[str, list[dict[str, str]]] = defaultdict(list)
    for q in raw["lms/mdl_quiz"]:
        quizzes[q["course"]].append(q)
    attempts: Counter[tuple[int, str]] = Counter()  # abandonada também consome tentativa
    per_user: Counter[int] = Counter()
    for a in raw["lms/mdl_quiz_attempts"]:
        attempts[(int(a["userid"]), a["quiz"])] += 1
        per_user[int(a["userid"])] += 1
    active: dict[int, list[str]] = defaultdict(list)  # só inscrição ativa de aluno
    for en in raw["lms/mdl_user_enrolments"]:
        if en["role"] == "student" and en["status"] == "0":
            active[int(en["userid"])].append(en["courseid"])
    by_enr: dict[Enr, list[int]] = defaultdict(list)
    for uid, v in oracle_lms_accounts(raw).items():
        by_enr[v].append(uid)
    return {
        "quizzes": quizzes,
        "attempts": attempts,
        "per_user": per_user,
        "active": active,
        "by_enr": by_enr,
    }


def oracle_lms(raw: Raw, enr: Enr, today: date = REFERENCE) -> LmsFacts:
    e = enrollments(raw)[enr]
    ix = _lms_index(raw)
    uids: list[int] = ix["by_enr"].get(enr, [])
    abertos = restante = sem = total = 0
    futuras: list[tuple[date, str]] = []
    for uid in uids:
        total += ix["per_user"][uid]
        for course in ix["active"].get(uid, []):
            for q in ix["quizzes"].get(course, []):
                n = ix["attempts"][(uid, q["id"])]
                op, cl = _brt_date(_epoch(q["timeopen"])), _brt_date(_epoch(q["timeclose"]))
                if q["name"] == "Practice Test":
                    if op <= today <= cl:
                        abertos += n == 0
                        restante += n < int(q["attempts"])
                else:  # "AVn - Online Exam": vale nota
                    if cl < today and n == 0:
                        sem += 1
                    if cl >= today:
                        futuras.append((op, q["name"].split(" ")[0]))
    prox: str | None = None
    fonte: str | None = None
    tipo: str | None = None
    if futuras:
        d, tipo = min(futuras)
        prox, fonte = br_date(d), "lms"
    elif e["MODALITY"] == "CAMPUS" and e["ENROLLMENT_STATUS"] == "ACTIVE":
        t = next(t for t in raw["sag/term"] if t["TERM"] == current_term(raw))
        cal = sorted(
            (date.fromisoformat(t[k]), k[:3])
            for k in ("AV1_START", "AV2_START")
            if date.fromisoformat(t[k]) >= today
        )
        if cal:
            prox, fonte, tipo = br_date(cal[0][0]), "calendario", cal[0][1]
    return LmsFacts(abertos, restante, prox, fonte, sem, total, tipo, bool(uids))


# ----------------------------------------------------------------------------- catálogo
@dataclass(frozen=True)
class CatalogCourse:
    nome: str
    ofertado: bool
    unidades: tuple[str, ...] = ()
    mensalidade: int | None = None  # centavos, preço vigente na data de referência
    ano: int | None = None
    parcelas_por_semestre: int | None = None


def oracle_catalog(raw: Raw, family: str, modality: str) -> dict[str, CatalogCourse]:
    """Cursos (sigla -> oferta) de uma marca-família e modalidade ("presencial" | "ead")."""
    ead = modality.strip().lower() in ("ead", "dl", "digital")
    brand = family + "_EAD" if ead else family
    units = {u["UNIT_ID"]: u["UNIT_NAME"] for u in raw["sag/unit"] if u["BRAND"] == brand}
    offered: dict[str, set[str]] = defaultdict(set)
    for o in raw["sag/course_offer"]:
        if o["UNIT_ID"] in units:
            offered[o["COURSE"]].add(units[o["UNIT_ID"]])
    company = next(
        c["companyCode"] for c in raw["finance/company"] if _slug(c["tradeName"]) == _slug(family)
    )
    installments = {p["productCode"]: p["installmentsPerTerm"] for p in raw["finance/product"]}
    out: dict[str, CatalogCourse] = {}
    for c in raw["sag/course"]:
        product = f"GRAD-{c['COURSE_ACRONYM']}-{'DIG' if ead else 'PRE'}"
        price = next(
            (
                p
                for p in raw["finance/price_list"]
                if p["productCode"] == product
                and p["companyCode"] == company
                and date.fromisoformat(p["validFrom"])
                <= REFERENCE
                <= date.fromisoformat(p["validTo"])
            ),
            None,
        )
        names = tuple(sorted(offered.get(c["COURSE"], ())))
        out[c["COURSE_ACRONYM"]] = CatalogCourse(
            nome=c["COURSE_NAME"],
            ofertado=bool(names),
            unidades=names,
            mensalidade=int(price["priceCents"]) if price and names else None,
            ano=REFERENCE.year if price and names else None,
            parcelas_por_semestre=int(installments[product])
            if names and product in installments
            else None,
        )
    return out


# ----------------------------------------------------------------------------- cobertura
def oracle_coverage(raw: Raw) -> dict[str, Any]:
    links = oracle_link_protocols(raw)
    ref_found = total_refs = 0
    protocols = {p["Protocol"] for p in raw["secretary/protocol"]}
    for h in raw["sag/enrollment_status_history"]:
        if h["PROTOCOL_REF"]:
            total_refs += 1
            ref_found += protocol_ref_digits(h["PROTOCOL_REF"]) in protocols
    accounts = oracle_lms_accounts(raw)
    student_accounts = [
        u
        for u in raw["lms/mdl_user"]
        if not _NOT_STUDENT.match(u["username"].strip().lower())
        and not u["student_id"].upper().startswith("PROF_")
    ]
    ce = contract_enrollment(raw)
    ens = enrollments(raw)
    return {
        "protocolos_total": len(links),
        "protocolos_ligados": sum(1 for r in links.values() if r.rule),
        "protocolos_sem_vinculo": sum(1 for r in links.values() if not r.rule),
        "protocolos_por_regra": dict(Counter(r.rule for r in links.values() if r.rule)),
        "contas_lms": len(accounts),
        "contas_lms_de_aluno": len(student_accounts),
        "snapshot": oracle_snapshot_check(raw),
        "protocol_ref": (ref_found, total_refs),
        "contratos_resolvidos": (sum(1 for v in ce.values() if v in ens), len(ce)),
        "matriculas": len(ens),
        "ra_dv_valido": (sum(1 for k in ens if ra_valid(k[1])), len(ens)),
        "desde": {
            "lms": br_date(
                _brt_date(
                    min(
                        _epoch(c["startdate"])
                        for c in raw["lms/mdl_course"]
                        if c["startdate"] != "0"
                    )
                )
            ),
            "secretaria": br_date(
                min(_iso_date(p["Opened_At"]) for p in raw["secretary/protocol"])
            ),
        },
    }


# ----------------------------------------------------------------------------- resposta ideal
_DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "data" / "dados.zip"
_RAW_BY_SOURCE: dict[str, Raw] = {}
ENROLLMENT_STATUS_PT = {
    "ACTIVE": "ativa",
    "LOCKED": "trancada",
    "CANCELLED": "cancelada",
    "DROPOUT": "abandonada",
    "GRADUATED": "concluída",
    "TRANSFERRED": "transferida",
}


def _raw_for(args: Mapping[str, Any], raw: Raw | None) -> Raw:
    if raw is not None:
        return raw
    src = str(args.get("source", _DEFAULT_SOURCE))
    if src not in _RAW_BY_SOURCE:
        _RAW_BY_SOURCE[src] = load_csv(Path(src))
    return _RAW_BY_SOURCE[src]


def ideal_answer(item: Mapping[str, Any], raw: Raw | None = None) -> tuple[str, list[str]]:
    """Resposta ideal em PT-BR e os fatos (já formatados) que ela contém.

    `item = {"oracle_fn": "oracle_financial", "args": {"enr": [brand, ra], "source": ...}}`.
    `source` (zip ou pasta) é opcional; sem ele vale `data/dados.zip`. `raw` já carregado tem
    precedência (testes sobre fixture).
    """
    fn, args = str(item["oracle_fn"]), item.get("args", {})
    r = _raw_for(args, raw)
    enr: Enr = (str(args["enr"][0]), str(args["enr"][1])) if "enr" in args else ("", "")
    if fn == "oracle_financial":
        f = oracle_financial(r, enr)
        if f.faturas_vencidas == 0:
            return "Você não tem faturas vencidas.", []
        facts = [money(f.total_para_quitar), str(f.faturas_vencidas), money(f.principal_vencido)]
        ans = (
            f"Para quitar tudo hoje você deve {facts[0]}, sendo {facts[2]} de principal vencido, "
            f"em {facts[1]} faturas vencidas."
        )
        if f.proximo_vencimento:
            d = br_date(f.proximo_vencimento)
            facts.append(d)
            ans += f" A próxima fatura vence em {d}."
        return ans, facts
    if fn == "oracle_lms":
        lms = oracle_lms(r, enr)
        facts = [str(lms.simulados_abertos), str(lms.simulados_com_tentativa_restante)]
        ans = (
            f"Você tem {facts[0]} simulados abertos sem tentativa e "
            f"{facts[1]} com tentativa restante."
        )
        if lms.proxima_avaliacao:
            facts.append(lms.proxima_avaliacao)
            ans += f" A próxima avaliação é em {lms.proxima_avaliacao} (fonte: {lms.fonte})."
        return ans, facts
    if fn == "oracle_forecast":
        fc = oracle_forecast(r, enr)
        if fc.previsao is None:
            return "Não há previsão de conclusão para esta matrícula.", []
        return f"A previsão de conclusão é {fc.previsao}, se você cursar regularmente.", [
            fc.previsao
        ]
    if fn == "oracle_protocols":
        ps = oracle_protocols(r, enr)
        abertos = [
            p["protocolo"] for p in ps if p["status"] in ("aberto", "em análise", "aguardando você")
        ]
        if not abertos:
            return "Você não tem protocolos em aberto.", []
        return f"Você tem {len(abertos)} protocolos em aberto: " + ", ".join(abertos) + ".", [
            str(len(abertos)),
            *abertos,
        ]
    if fn == "oracle_courses":
        c = oracle_courses(r, enr)
        pct = fmt_decimal1(c.conclusao_pct)
        st = ENROLLMENT_STATUS_PT[c.status]
        return f"Sua matrícula está {st}, com {pct}% do curso concluído.", [st, pct]
    if fn == "oracle_catalog":
        cat = oracle_catalog(r, str(args["family"]), str(args["modality"]))[str(args["course"])]
        if not cat.ofertado:
            return f"O curso de {cat.nome} não é oferecido nessa modalidade.", []
        m = money(cat.mensalidade or 0)
        return f"O curso de {cat.nome} é oferecido; a mensalidade em {cat.ano} é {m}.", [
            m,
            str(cat.ano),
        ]
    raise ValueError(f"oracle_fn desconhecida: {fn}")


# ----------------------------------------------------------------------------- perturbação
_MONEY = re.compile(r"R\$ \d{1,3}(?:\.\d{3})*,\d{2}")
_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_GRADE = re.compile(r"(?<![\w.,])\d{1,2},\d(?![\d,])")
_NUMBER = re.compile(r"(?<![\w.,/$])\d+(?![\w.,/]|\s*%)")


def _spans(text: str, kind: str) -> list[tuple[int, int]]:
    """Posições dos fatos de um tipo; números soltos ignoram dinheiro, datas e notas."""
    if kind == "money":
        return [m.span() for m in _MONEY.finditer(text)]
    if kind == "date":
        return [m.span() for m in _DATE.finditer(text)]
    if kind == "grade":
        return [m.span() for m in _GRADE.finditer(text)]
    if kind == "number":
        masked = text
        for rx in (_MONEY, _DATE, _GRADE):
            masked = rx.sub(lambda m: "#" * len(m.group()), masked)
        return [m.span() for m in _NUMBER.finditer(masked)]
    raise ValueError(f"tipo de fato desconhecido: {kind}")


def mutate_one_fact(answer: str, kind: str, rng: random.Random) -> str:
    """Troca exatamente um fato (`money`, `date`, `grade` ou `number`) por outro valor."""
    spans = _spans(answer, kind)
    if not spans:
        raise ValueError(f"nenhum fato do tipo {kind!r} em {answer!r}")
    a, b = spans[rng.randrange(len(spans))]
    old = answer[a:b]
    if kind == "money":
        cents = int(old[3:].replace(".", "").replace(",", ""))
        new = money(
            cents + rng.choice((-1, 1)) * rng.randint(1, max(2, cents // 4))
            if cents > 1
            else cents + 7
        )
    elif kind == "date":
        d = datetime.strptime(old, "%d/%m/%Y").date()
        new = br_date(d + timedelta(days=rng.choice((-1, 1)) * rng.randint(1, 30)))
    elif kind == "grade":
        n = int(old.replace(",", ""))
        v = (n + rng.choice((-1, 1)) * rng.randint(1, 9)) % 101
        new = f"{v // 10},{v % 10}"
    else:
        n = int(old)
        new = str(n + rng.choice((-1, 1)) * rng.randint(1, 3) if n > 3 else n + rng.randint(1, 3))
    if new == old:
        new = money(1) if kind == "money" else str(rng.randint(100, 999))
    return answer[:a] + new + answer[b:]
