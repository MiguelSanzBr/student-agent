import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.data.queries import academic as q
from app.rules.academic import Progress, graduation_forecast
from app.rules.coverage import coverage_note
from app.tools.base import (
    ENROLLMENT,
    EntityRecord,
    Tool,
    ToolContext,
    ToolOutput,
    ToolSpec,
    default_ontology,
    resolve_enrollment,
    schema,
    validate_args,
)
from core.format import fmt_date, fmt_grade, fmt_pct
from core.normalize import SP

MAX_ITEMS = 20
EMPTY = "Nenhum registro encontrado."
DONE = ("APPROVED", "EXEMPT")  # dispensa conta como cumprida na matriz


def _pt(enum: str, value: Any) -> str:
    try:
        return default_ontology().label_pt(enum, str(value))
    except KeyError:
        return str(value)


def _ascii(s: str) -> str:
    d = unicodedata.normalize("NFKD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold().strip()


def _subject_match(query: str, name: str, code: str) -> bool:
    nq, nn = _ascii(query), _ascii(name)
    if nq == code.casefold() or nq == nn:
        return True
    names = nn.split()
    return all(any(w.startswith(t) for w in names) for t in nq.split())


def _subject_filter(query: str, rows: list[q.Row], key_name: str) -> list[q.Row]:
    """Nome PT sem acento, por prefixo ou token; um nome exato vence os demais (II vs III)."""
    hits = [r for r in rows if _subject_match(query, r[key_name], r["subject_code"])]
    nq = _ascii(query)
    exact = [r for r in hits if _ascii(r[key_name]) == nq or r["subject_code"].casefold() == nq]
    return exact or hits


def _subject_record(code: str, name: str, fields: Mapping[str, str]) -> EntityRecord:
    return EntityRecord("disciplina", code, tuple(dict.fromkeys((name, _ascii(name)))), fields)


def _date(v: date | datetime) -> str:
    return fmt_date(v.astimezone(SP).date() if isinstance(v, datetime) else v)


def _enrollment_record(idx: str, label: str, fields: Mapping[str, str]) -> EntityRecord:
    return EntityRecord("matricula", idx, (label, f"matrícula {idx}"), fields)


def _others(ctx: ToolContext, chosen: str) -> tuple[list[dict[str, str]], list[EntityRecord]]:
    data, recs = [], []
    for e in ctx.scope.enrollments:
        if e.index == chosen:
            continue
        st = _pt("enrollment_status", e.status)
        data.append({"indice": e.index, "rotulo": e.label, "status": st})
        recs.append(_enrollment_record(e.index, e.label, {"status": st}))
    return data, recs


async def get_academic_profile(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    a = validate_args(PROFILE.spec, args)
    ref = resolve_enrollment(ctx.scope, a.get("enrollment"), None)
    async with ctx.db() as conn:
        e = await q.enrollment(conn, ref.enrollment_key)
        events = await q.events(conn, ref.enrollment_key)
        regs = await q.term_registrations(conn, ref.enrollment_key)
        docs = await q.documents(conn, ref.enrollment_key)
    status = _pt("enrollment_status", e["status"])
    d: dict[str, Any] = {
        "status": status,
        "data_status": _date(e["status_date"]),
        "curso": e["course_name"],
        "matriz": e["curriculum_id"],
        "unidade": e["unit_label"] or "",
        "modalidade": _pt("modality", e["modality"]),
        "turno": _pt("shift", e["shift"]),
        "semestre": str(int(e["semester"])),
        "ingresso": _date(e["enrolled_on"]),
        "semestre_ingresso": e["entry_term"],
        "tipo_ingresso": _pt("entry_type", e["entry_type"]),
    }
    if e["gpa"] is not None:
        d["cr"] = fmt_grade(e["gpa"])
    if e["completion_pct"] is not None:
        d["conclusao"] = fmt_pct(e["completion_pct"])
    if e["origin_brand"]:
        d["origem"] = e["origin_brand"]
    d["historico"] = [
        {
            "data": _date(h["event_date"]),
            "semestre": h["term"],
            **({"de": _pt("enrollment_status", h["old_status"])} if h["old_status"] else {}),
            "para": _pt("enrollment_status", h["new_status"]),
            "motivo": _pt("status_reason", h["reason"]),
        }
        for h in events
    ]
    # o status da matrícula prevalece: rematrícula só é informada para matrícula ativa
    if regs and e["status"] == "ACTIVE":
        d["rematricula"] = f"{_pt('term_status', regs[0]['term_status'])} em {regs[0]['term']}"
    elif regs:
        d["observacao_rematricula"] = (
            f"A situação da matrícula ({status.lower()}) prevalece sobre o registro de rematrícula."
        )
    d["documentos"] = [
        {"tipo": _pt("document_type", x["document_type"]), "emitido_em": _date(x["issued_at"]),
         "entrega": _pt("delivery", x["delivery"])}
        for x in docs
    ]  # fmt: skip
    records = [
        _enrollment_record(ref.index, ref.label, {k: v for k, v in d.items() if isinstance(v, str)})
    ]
    records += [
        EntityRecord("documento", x["document_id"], (doc["tipo"],),
                     {"emitido_em": doc["emitido_em"], "entrega": doc["entrega"]})
        for x, doc in zip(docs, d["documentos"], strict=True)
    ]  # fmt: skip
    if len(ctx.scope.enrollments) > 1:
        d["matricula"] = ref.label
        if ctx.offer_other_enrollment:
            d["outras_matriculas"], extra = _others(ctx, ref.index)
            records += extra
    return ToolOutput(d, tuple(records))


async def list_course_results(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    a = validate_args(RESULTS.spec, args)
    ref = resolve_enrollment(ctx.scope, a.get("enrollment"), None)
    term, subject = a.get("term"), a.get("subject")
    notes: list[str] = []
    records: list[EntityRecord] = []
    async with ctx.db() as conn:
        rows = await q.results(conn, ref.enrollment_key, term, a.get("result"))
        first, since = await q.first_term_start(conn)
        grid = None
        if subject:
            e = await q.enrollment(conn, ref.enrollment_key)
            grid = await q.curriculum(conn, e["curriculum_id"])
    if subject:
        rows = _subject_filter(subject, rows, "subject_name_pt")
    if not rows:
        if term and term < first:
            notes.append(coverage_note("SAG", since) + " Não há notas desse período.")
            records.append(EntityRecord("cobertura", "sag", ("SAG",), {"desde": fmt_date(since)}))
        elif subject and not _subject_filter(subject, grid or [], "name_pt"):
            notes.append(f'"{subject}" não faz parte da sua matriz curricular.')
        else:
            notes.append(EMPTY)
    shown = rows[:MAX_ITEMS]
    items: list[dict[str, str]] = []
    for r in shown:
        it = {
            "disciplina": r["subject_name_pt"], "codigo": r["subject_code"], "semestre": r["term"],
            "resultado": _pt("class_result_status", r["result"]),
        }  # fmt: skip
        for key, col in (("av1", "grade_av1"), ("av2", "grade_av2"), ("av3", "grade_av3"),
                         ("media_final", "final_grade")):  # fmt: skip
            if r[col] is not None:
                it[key] = fmt_grade(r[col])
        if r["attendance_pct"] is not None:
            it["frequencia"] = fmt_pct(r["attendance_pct"])
        if r["professor"]:
            it["professor"] = r["professor"]
        if r["divergencia"]:
            it["divergencia"] = (
                "sim: reprovado por frequência, mas a frequência registrada é de 75% ou mais"
            )
        items.append(it)
        if r["subject_code"] not in {x.id for x in records}:  # 1º = tentativa mais recente
            records.append(_subject_record(r["subject_code"], r["subject_name_pt"], it))
    d: dict[str, Any] = {"total": len(rows), "disciplinas": items}
    if len(rows) > len(shown):
        d["aviso_limite"] = f"Mostrando as {MAX_ITEMS} mais recentes de {len(rows)}."
    if notes:
        d["aviso"] = " ".join(notes)
    if len(ctx.scope.enrollments) > 1:
        d["matricula"] = ref.label
    return ToolOutput(d, tuple(records))


async def get_curriculum_progress(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    a = validate_args(PROGRESS.spec, args)
    ref = resolve_enrollment(ctx.scope, a.get("enrollment"), None)
    async with ctx.db() as conn:
        e = await q.enrollment(conn, ref.enrollment_key)
        grid = await q.curriculum(conn, e["curriculum_id"])
        total_hours = await q.curriculum_hours(conn, e["curriculum_id"])
        res = await q.results(conn, ref.enrollment_key)
        term = await q.current_term(conn)
    done = {r["subject_code"] for r in res if r["result"] in DONE}
    ongoing = {r["subject_code"] for r in res if r["result"] == "IN_PROGRESS"} - done
    pending = [s for s in grid if s["mandatory"] and s["subject_code"] not in done]
    items: list[dict[str, str]] = []
    records: list[EntityRecord] = []
    for s in pending[:MAX_ITEMS]:
        it = {
            "disciplina": s["name_pt"], "codigo": s["subject_code"],
            "semestre_matriz": str(int(s["semester"])),
            "situacao": "cursando" if s["subject_code"] in ongoing else "pendente",
        }  # fmt: skip
        if s["prerequisite_code"]:
            met = "cumprido" if s["prerequisite_code"] in done else "não cumprido"
            it["pre_requisito"] = f"{s['prerequisite_name']} ({met})"
        items.append(it)
        records.append(_subject_record(s["subject_code"], s["name_pt"], it))
    hours_done = sum(Decimal(s["hours"]) for s in grid if s["subject_code"] in done)
    d: dict[str, Any] = {
        "curso": e["course_name"],
        "matriz": e["curriculum_id"],
        "conclusao": fmt_pct(e["completion_pct"]) if e["completion_pct"] is not None else "",
        "disciplinas_cumpridas": str(len(done & {s["subject_code"] for s in grid})),
        "disciplinas_na_matriz": str(len(grid)),
        "carga_cumprida_horas": str(int(hours_done)),
        "carga_total_horas": str(int(total_hours)) if total_hours is not None else "",
        "pendentes_total": len(pending),
        "pendentes": items,
    }
    if len(pending) > len(items):
        d["aviso_limite"] = f"Mostrando as {MAX_ITEMS} primeiras de {len(pending)} pendentes."
    if e["status"] == "ACTIVE":  # semestre corrente conta: já cursados = nível - 1
        prog = Progress(max(0, int(e["semester"]) - 1), int(e["duration_semesters"]))
        d["previsao"] = graduation_forecast(prog, term)
    if len(ctx.scope.enrollments) > 1:
        d["matricula"] = ref.label
    return ToolOutput(d, tuple(records))


PROFILE = Tool(
    ToolSpec(
        "get_academic_profile",
        "Perfil acadêmico da matrícula: curso, situação, período e progresso.",
        schema({"enrollment": ENROLLMENT}),
    ),
    get_academic_profile,
    "academico",
)
RESULTS = Tool(
    ToolSpec(
        "list_course_results",
        "Lista resultados por disciplina (nota, frequência, situação).",
        schema(
            {
                "enrollment": ENROLLMENT,
                "term": {"type": "string", "description": "Semestre, ex.: 2026/1"},
                "result": {"type": "string", "enum": ["aprovado", "reprovado", "cursando"]},
                "subject": {"type": "string", "description": "Nome da disciplina"},
            }
        ),
    ),
    list_course_results,
    "academico",
)
PROGRESS = Tool(
    ToolSpec(
        "get_curriculum_progress",
        "Progresso na matriz curricular da matrícula.",
        schema({"enrollment": ENROLLMENT}),
    ),
    get_curriculum_progress,
    "academico",
)
TOOLS: tuple[Tool, ...] = (PROFILE, RESULTS, PROGRESS)
