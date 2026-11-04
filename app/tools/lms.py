import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from app.data.queries import lms as q
from app.rules.coverage import coverage_note
from app.rules.lms import Attempt, Quiz, lms_summary, quiz_status
from app.tools.base import (
    ENROLLMENT,
    EntityRecord,
    Tool,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    resolve_enrollment,
    schema,
)
from core.format import fmt_date, fmt_instant
from core.normalize import SP

MAX_ITEMS = 20
_PERIOD = re.compile(r"(\d{4})(?:/([12]))?")
_SITUACAO = {
    "aberta": "aberta",
    "realizada": "realizada",
    "encerrada_sem_tentativa": "encerrada sem tentativa",
    "futura": "futura",
}
_AVISO_TENTATIVAS = "As tentativas feitas no AVA (inclusive simulados) não valem nota oficial."


async def _window(conn: Any, period: str | None) -> tuple[str, date, date]:
    """Período pedido (semestre ou ano) → (rótulo, início, fim); sem pedido, o semestre vigente."""
    if period is None:
        w = await q.term_window(conn, None)
        if w is None:
            raise ToolError("PERIODO_INEXISTENTE")
        return w
    m = _PERIOD.fullmatch(period.strip())
    if m is None:
        raise ToolError("ARGUMENTO_INVALIDO")
    year, half = int(m[1]), m[2]
    if half is None:
        return m[1], date(year, 1, 1), date(year, 12, 31)
    if (w := await q.term_window(conn, period.strip())) is not None:
        return w
    # shortcut: semestre fora da tabela `term` vira meio ano civil; aceitável porque só a contagem
    # de acessos o usa e o domínio começa em 2023.
    return period.strip(), *(
        (date(year, 1, 1), date(year, 6, 30))
        if half == "1"
        else (date(year, 7, 1), date(year, 12, 31))
    )


def _prova(quiz: Quiz, subject: str, status: str, n: int) -> EntityRecord:
    opens, closes = fmt_date(quiz.opens), fmt_date(quiz.closes)
    return EntityRecord(
        "prova", f"{subject} {quiz.kind} {opens}", (subject, quiz.kind),
        {"situacao": _SITUACAO[status], "janela_inicio": opens, "janela_fim": closes,
         "tentativas": str(n), "tentativas_permitidas": str(quiz.allowed_attempts),
         "vale_nota": "sim" if quiz.counts_for_grade else "não"},
    )  # fmt: skip


async def get_lms_activity(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    ref = resolve_enrollment(ctx.scope, args.get("enrollment"), None)
    async with ctx.db() as conn:
        label, start, end = await _window(conn, args.get("period"))
        since = await q.lms_coverage(conn)
        n_accounts, last = await q.last_access(conn, ref.enrollment_key)
        data: dict[str, Any] = {"matricula": ref.label, "periodo": label}
        cobertura = EntityRecord("cobertura", "lms", ("AVA", "LMS"), {"desde": fmt_date(since)})
        if n_accounts == 0:
            data["mensagem"] = (
                "Esta matrícula não tem conta no AVA (ambiente virtual de aprendizagem)."
            )
            return ToolOutput(data, (cobertura,))
        before = end < since
        if before or start < since:
            data["cobertura"] = coverage_note("lms", since)
        rooms = await q.rooms(conn, ref.enrollment_key)
        rows = await q.quizzes(conn, ref.enrollment_key)
        taken = await q.attempts(conn, ref.enrollment_key)
        cal = await q.calendar(conn) if ref.status == "ACTIVE" else {}
        n_logins = 0 if before else await q.logins(conn, ref.enrollment_key, start, end)

    quizzes = {
        str(r[0]): (
            Quiz(str(r[0]), r[2].astimezone(SP).date(), r[3].astimezone(SP).date(), int(r[4]),
                 r[1] != "simulado", r[1]),
            r[5],
        )
        for r in rows
    }  # fmt: skip
    attempts = [Attempt(str(i)) for i in taken]
    s = lms_summary(
        (v[0] for v in quizzes.values()), attempts, ctx.today, modality=ref.modality, calendar=cal
    )
    nxt = s.proxima_avaliacao
    data["ultimo_acesso"] = fmt_instant(last) if last else "sem acesso registrado"
    if before:
        data["mensagem_acessos"] = data["cobertura"]
    elif n_logins:
        data["acessos_no_periodo"] = n_logins
    else:
        data["mensagem_acessos"] = (
            f"Nenhum acesso registrado em {label} (dados do AVA desde {fmt_date(since)})."
        )
    data["disciplinas"], data["total_disciplinas"] = rooms[:MAX_ITEMS], len(rooms)
    data["simulados_abertos"] = s.simulados_abertos
    data["simulados_com_tentativa_restante"] = s.simulados_com_tentativa_restante
    data["pendentes"] = s.pendentes
    data["tentativas_nao_oficiais"] = len(attempts)
    data["aviso_tentativas"] = _AVISO_TENTATIVAS
    if nxt:
        data["proxima_avaliacao"] = {
            "data": fmt_date(nxt.date),
            "tipo": nxt.kind,
            "fonte": nxt.source,
        }
    else:
        data["proxima_avaliacao"] = None
        data["mensagem_proxima_avaliacao"] = (
            "Não há próxima avaliação agendada para esta matrícula."
        )

    counts = {qid: sum(a.quiz_id == qid for a in attempts) for qid in quizzes}
    # abertas/futuras primeiro (a que fecha antes), depois as demais, as mais recentes antes
    items = []
    for quiz, subject in quizzes.values():
        st = quiz_status(quiz, attempts, ctx.today)
        live = st in ("aberta", "futura")
        items.append(
            ((not live, quiz.closes if live else -quiz.closes.toordinal()), quiz, subject, st)
        )
    items.sort(key=lambda t: (t[0][0], t[0][1]))
    recs = tuple(_prova(qz, sub, st, counts[qz.id]) for _, qz, sub, st in items[:MAX_ITEMS])
    data["provas"] = [
        {"disciplina": r.aliases[0], "tipo": r.aliases[1], **{k: v for k, v in r.fields.items()}}
        for r in recs
    ]
    data["total_provas"] = len(items)
    f = {
        "acessos": str(n_logins), "ultimo_acesso": data["ultimo_acesso"],
        "simulados_abertos": str(s.simulados_abertos),
        "simulados_com_tentativa_restante": str(s.simulados_com_tentativa_restante),
        "pendentes": str(s.pendentes), "tentativas_nao_oficiais": str(len(attempts)),
    }  # fmt: skip
    if nxt:
        f |= {
            "proxima_data": fmt_date(nxt.date),
            "proxima_tipo": nxt.kind,
            "proxima_fonte": nxt.source,
        }
    lms_rec = EntityRecord("lms", label, (label,), f)
    return ToolOutput(data, (lms_rec, cobertura, *recs))


TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "get_lms_activity",
            "Atividade no AVA: acessos, simulados, próxima avaliação e pendências.",
            schema(
                {
                    "enrollment": ENROLLMENT,
                    "period": {"type": "string", "description": "Período, ex.: 2026/2"},
                }
            ),
        ),
        get_lms_activity,
        "lms",
    ),
)
