import secrets
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.data.queries import protocols as q
from app.rules.coverage import coverage_note
from app.rules.eligibility import (
    EligibilityFacts,
    Eligible,
    NotEligible,
    OpenRequest,
    SubjectFact,
    eligibility,
)
from app.rules.finance import COUNTED, Invoice, Payment, financial_position
from app.rules.protocols import protocol_status_pt, sla_due
from app.tools.base import (
    ENROLLMENT,
    FERRAMENTA_INDISPONIVEL,
    TAINT_NO_DRAFT,
    DraftCard,
    DraftKey,
    EntityRecord,
    Tool,
    ToolContext,
    ToolOutput,
    ToolSpec,
    default_ontology,
    norm,
    orientation,
    resolve_enrollment,
    schema,
    service_record,
    validate_args,
)
from core.format import fmt_brl, fmt_date, fmt_instant
from core.normalize import SP

PLACEHOLDER = "[mensagem não exibida]"
NOT_FOUND = "Protocolo não encontrado."
WARNING = "Confira os dados: o pedido só é aberto depois que você clicar em Confirmar."
_PREFER = {"DOC-040": "GRADUATED", "DOC-041": "GRADUATED", "ACA-011": "LOCKED"}
_BY_SUBJECT = {"ACA-020", "ACA-021"}
_ROLE = {"responsavel": "responsável"}


def _args(name: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Valida contra o schema da própria ferramenta (campo ausente ou fora do enum = recusa)."""
    return validate_args(next(t.spec for t in TOOLS if t.spec.name == name), args)


def _brt(dt: datetime) -> date:
    return dt.astimezone(SP).date()


def _row(
    number: str, code: str, status: str, opened: date, prazo: date | None, who: str
) -> dict[str, str]:
    try:
        name = default_ontology().service(code).name_pt
    except KeyError:  # um código fora da ontologia não derruba a lista
        name = "Serviço da secretaria"
    return {
        "protocolo": number, "servico": name,
        "status": status, "aberto_em": fmt_date(opened),
        "prazo": fmt_date(prazo) if prazo else "não informado",
        "solicitante": who,
    }  # fmt: skip


def _record(r: Mapping[str, str]) -> EntityRecord:
    f = {k: r[k] for k in ("servico", "status", "aberto_em", "prazo")}
    return EntityRecord("protocolo", r["protocolo"], (r["protocolo"],), f)


def _secretary(r: tuple[Any, ...]) -> dict[str, str]:
    number, code, status, opened_at, who = r
    opened = _brt(opened_at)
    try:
        prazo: date | None = sla_due(opened, default_ontology().service(code).sla_hours)
    except KeyError:
        prazo = None
    return _row(number, code, protocol_status_pt(status), opened, prazo, _ROLE.get(who, who))


def _sa(r: tuple[Any, ...]) -> dict[str, str]:
    return _row(r[0], r[1], "aberto", r[2], r[3], "aluno")


async def _list(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    args = _args("list_protocols", args)
    async with ctx.db() as conn:
        rows = [_secretary(r) for r in await q.list_secretary(conn)]
        rows += [_sa(r) for r in await q.list_sa(conn)]
        since = await q.coverage_start(conn, "secretaria")
    if (want := args.get("status")) is not None:  # aceita o rótulo PT ou o status cru
        ok = {str(want).strip().casefold(), protocol_status_pt(str(want).strip()).casefold()}
        rows = [r for r in rows if r["status"].casefold() in ok]
    if not rows:
        data: dict[str, Any] = {"mensagem": "Nenhum registro encontrado."}
        if since is not None:
            data["nota_cobertura"] = coverage_note("secretaria", since)
        return ToolOutput(data)
    return ToolOutput(
        {"protocolos": [{k: v for k, v in r.items()} for r in rows]},
        tuple(_record(r) for r in rows),
    )


async def _get(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    numero = str(_args("get_protocol", args)["numero"])
    onto = default_ontology()
    async with ctx.db() as conn:
        if numero.startswith("SA-"):
            sa = await q.sa_protocol(conn, numero)
            row = _sa(sa) if sa else None
        else:
            sec = await q.secretary_protocol(conn, numero)
            row = _secretary(sec) if sec else None
        if row is None:
            return ToolOutput({"mensagem": NOT_FOUND})
        data: dict[str, Any] = dict(row)
        untrusted = False
        if not numero.startswith("SA-"):
            data["historico"] = [
                {
                    "de": protocol_status_pt(a) if a else "",
                    "para": protocol_status_pt(b),
                    "em": fmt_instant(at),
                }
                for a, b, at in await q.events(conn, numero)
            ]
            msgs = []
            for autor, modelo, texto_livre, at in await q.messages(conn, numero):
                untrusted = untrusted or modelo is None or texto_livre is not None
                msgs.append(
                    {
                        "autor": onto.label_pt("author_role", autor),
                        "mensagem": modelo if modelo is not None else PLACEHOLDER,
                        "em": fmt_instant(at),
                    }
                )
            data["mensagens"] = msgs
            data["documentos"] = [
                {
                    "tipo": onto.label_pt("document_type", t),
                    "emitido_em": fmt_date(_brt(at)),
                    "entrega": onto.label_pt("delivery", d),
                }
                for t, at, d in await q.documents(conn, numero)
            ]
            if inv := await q.linked_invoice(conn, numero):
                i, comp, st, due, net = inv
                data["fatura_vinculada"] = {
                    "fatura": i, "competencia": comp, "vencimento": fmt_date(due),
                    "status": onto.label_pt("invoice_status", st), "valor": fmt_brl(net),
                }  # fmt: skip
    return ToolOutput(data, (_record(row),), untrusted=untrusted)


def _allowed(ctx: ToolContext) -> str | None:
    if ctx.channel == "a2a":
        return FERRAMENTA_INDISPONIVEL
    return TAINT_NO_DRAFT if ctx.tainted else None


def _resolve_subject(
    mention: str, rows: list[tuple[Any, ...]], term: str | None
) -> tuple[str | None, str | None]:
    """(código, recusa): casa por código ou prefixo do nome PT, sem acento; no empate vale o período corrente."""  # noqa: E501
    m = norm(mention)
    names = {r[0]: r[1] for r in rows}
    hit = [c for c in names if norm(c) == m] or [
        c for c, n in names.items() if norm(n).startswith(m)
    ]
    if len(hit) > 1:  # o período corrente só desempata se sobrar alguma
        hit = [c for c in hit if any(r[0] == c and r[2] == term for r in rows)] or hit
    if not hit:
        return None, "Não encontrei essa disciplina no seu histórico."
    if len(hit) > 1:
        return None, "Mais de uma disciplina combina com esse nome; diga o nome completo."
    return hit[0], None


def _subject_facts(rows: list[tuple[Any, ...]], term: str | None) -> dict[str, SubjectFact]:
    out: dict[str, SubjectFact] = {}
    for code, _name, t, result, graded in sorted(rows, key=lambda r: (r[2] == term, r[2])):
        out[code] = SubjectFact(result, bool(graded) and t == term)  # o corrente vence
    return out


def _refusal(motivo: str, alt: str | None = None) -> ToolOutput:
    data = {"motivo": motivo}
    if alt is None:
        return ToolOutput(data)
    data["alternativa"] = default_ontology().service(alt).ui_label
    return ToolOutput(data, (service_record(alt),))


async def check_eligibility(
    conn: Any,
    code: str,
    svc: Any,
    enr: Any,
    subject: str | None,
    today: date,
    rows: list[tuple[Any, ...]],
    term: str | None,
) -> Eligible | NotEligible:
    """Regras de elegibilidade (spec §5.5) com os fatos do banco; usada no rascunho e na confirmação."""  # noqa: E501
    invs = [
        Invoice(str(i), int(n), s, d) for i, n, s, d in await q.invoices(conn, enr.enrollment_key)
    ]
    pays = [
        Payment(str(i), int(p), 0, 0, s) for i, p, s in await q.payments(conn, enr.enrollment_key)
    ]
    pos = financial_position(invs, pays, [], today)
    facts = EligibilityFacts(
        today=today,
        sla_hours=svc.sla_hours,
        enrollment_status=enr.status,
        diploma_issued=await q.diploma_issued(conn, enr.enrollment_key),
        has_open_invoice=any(i.status in COUNTED for i in invs),
        overdue_principal=pos.overdue_principal,
        subjects=_subject_facts(rows, term),
        open_requests=tuple(
            OpenRequest(c, None, "secretaria", _brt(at))
            for c, at in await q.open_secretary(conn, enr.enrollment_key)
        )
        + tuple(OpenRequest(c, s, "SA", d) for c, s, d in await q.open_sa(conn, enr.index)),
    )  # noqa: E501
    return eligibility(code, facts, subject=subject)


async def _draft(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    args = _args("draft_protocol", args)
    code, onto = str(args["service_code"]), default_ontology()
    try:
        svc = onto.service(code)
    except KeyError:
        return ToolOutput({"motivo": "Serviço não encontrado."})
    if svc.mode != "agent":  # só orienta (ACA-010/012/013/014) e oferece a secretaria
        return ToolOutput(
            {**orientation(code), "alternativa": onto.service("GEN-090").ui_label},
            (service_record(code), service_record("GEN-090")),
        )
    enr = resolve_enrollment(ctx.scope, args.get("enrollment"), _PREFER.get(code))
    sub, subject = ctx.scope.person_key, None
    async with ctx.db() as conn:
        rows: list[tuple[Any, ...]] = []
        term = await q.current_term(conn)
        if code in _BY_SUBJECT:
            rows = await q.class_results(conn, enr.enrollment_key)
            if mention := str(args.get("subject") or "").strip():
                subject, why = _resolve_subject(mention, rows, term)
                if why:
                    return _refusal(why)
        key = DraftKey(code, subject)
        if await ctx.drafts.same_pending(sub, ctx.conversation_id, key) is not None:
            return ToolOutput({"rascunho_ja_exibido": True})
        verdict = await check_eligibility(conn, code, svc, enr, subject, ctx.today, rows, term)
    if not isinstance(verdict, Eligible):
        return _refusal(verdict.motivo_pt, verdict.alternativa)
    due = fmt_date(sla_due(ctx.today, svc.sla_hours))
    card = DraftCard(
        draft_id=uuid.uuid4().hex,
        token=secrets.token_urlsafe(32),
        key=key,
        service_name_pt=svc.ui_label,
        enrollment_label=enr.label if len(ctx.scope.enrollments) > 1 else None,
        fee=fmt_brl(svc.fee_cents),
        due_date=due,
        description=str(args.get("description") or "")[:500],
        warning=WARNING,
        conversation_id=ctx.conversation_id,
        enrollment_index=enr.index,
    )
    await ctx.drafts.put(sub, ctx.conversation_id, card)
    data: dict[str, Any] = {
        "rascunho_exibido": True, "servico": svc.ui_label, "taxa": card.fee, "prazo": due,
    }  # fmt: skip
    if code == "GEN-090":
        data["orientacao"] = f"{svc.ui_label}: prazo de {orientation(code)['prazo']}."
    return ToolOutput(data, (service_record(code),), draft=card)


TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "list_protocols",
            "Lista os protocolos do aluno (sem descrições).",
            schema({"status": {"type": "string", "description": "Situação do protocolo"}}),
        ),
        _list,
        "protocolos",
    ),
    Tool(
        ToolSpec(
            "get_protocol",
            "Detalha um protocolo do aluno pelo número.",
            schema(
                {
                    "numero": {
                        "type": "string",
                        "pattern": r"^(\d{11}|SA-\d{4}-\d{6})$",
                        "description": "Número do protocolo",
                    }
                },
                ("numero",),
            ),
        ),
        _get,
        "protocolos",
    ),
    Tool(
        ToolSpec(
            "draft_protocol",
            "Prepara o rascunho de um protocolo para confirmação do aluno no web.",
            schema(
                {
                    "service_code": {
                        "type": "string",
                        "description": "Código do serviço, ex.: DOC-001",
                    },
                    "enrollment": ENROLLMENT,
                    "subject": {
                        "type": "string",
                        "description": "Nome ou código da disciplina, quando o serviço exigir",
                    },
                    "description": {
                        "type": "string",
                        "maxLength": 500,
                        "description": "Descrição do pedido",
                    },
                },
                ("service_code",),
            ),
        ),
        _draft,
        "protocolos",
        read_only=False,
        allowed=_allowed,
    ),
)
