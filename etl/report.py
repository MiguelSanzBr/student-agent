"""Relatório de qualidade dos dados: igualdade por regra, invariantes, contagens e oráculo.

Nunca grava CPF nem outro valor pessoal: só contagens, nomes de regra e rótulos.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

_LMS_SQL = """
WITH att AS (
  SELECT enrollment_key, quiz_id, count(*) AS n FROM canon.lms_quiz_attempt GROUP BY 1, 2
), q AS (
  SELECT q.quiz_type, q.attempts_allowed, coalesce(a.n, 0) AS n,
         (q.opens_at AT TIME ZONE 'America/Sao_Paulo')::date AS op,
         (q.closes_at AT TIME ZONE 'America/Sao_Paulo')::date AS cl
  FROM canon.lms_quiz q
  LEFT JOIN att a ON a.enrollment_key = q.enrollment_key AND a.quiz_id = q.quiz_id
  WHERE (%(e)s::uuid IS NULL OR q.enrollment_key = %(e)s::uuid)
)
SELECT
  count(*) FILTER (WHERE quiz_type = 'simulado' AND op <= %(t)s AND %(t)s <= cl AND n = 0),
  count(*) FILTER (WHERE quiz_type = 'simulado' AND op <= %(t)s AND %(t)s <= cl
                   AND n < attempts_allowed),
  count(*) FILTER (WHERE quiz_type IN ('AV1', 'AV2') AND cl < %(t)s AND n = 0)
FROM q
"""


def lms_sql_facts(
    conn: psycopg.Connection, today: date, enrollment_key: str | None = None
) -> dict[str, int]:
    """Fatos do LMS lidos de `canon` (todas as matrículas, ou uma): simulados e tentativas."""
    p = {"e": enrollment_key, "t": today}
    a, b, c = conn.execute(_LMS_SQL, p).fetchone() or (0, 0, 0)
    row = conn.execute(
        "SELECT count(*) FROM canon.lms_quiz_attempt "
        "WHERE (%(e)s::uuid IS NULL OR enrollment_key = %(e)s::uuid)",
        p,
    ).fetchone()
    return {
        "simulados_abertos": a,
        "simulados_com_tentativa_restante": b,
        "encerradas_sem_tentativa": c,
        "tentativas": int(row[0]) if row else 0,
    }


@dataclass(frozen=True)
class Check:
    name: str
    memory: Any
    oracle: Any


@dataclass
class LoadReport:
    memory_counts: dict[str, int]
    sql_counts: dict[str, int]
    checks: list[Check] = field(default_factory=list)
    invariants: dict[str, bool] = field(default_factory=dict)
    resolution: dict[str, dict[str, int]] = field(default_factory=dict)
    protocols_by_rule: dict[str, int] = field(default_factory=dict)
    protocols_unlinked: int = 0
    protocol_ref: tuple[int, int] = (0, 0)
    snapshot: tuple[int, int] = (0, 0)
    lms: dict[str, int] = field(default_factory=dict)
    logins_absent: int = 0
    logins_present: int = 0
    coverage: list[dict[str, str | None]] = field(default_factory=list)


def ok(b: object) -> str:
    return "ok" if b else "FALHA"


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def render_markdown(r: LoadReport) -> str:
    out = [
        "# Qualidade dos dados (carga)",
        "",
        "Gerado pelo ETL na própria carga. Nenhum CPF, e-mail, telefone ou nome aparece aqui.",
        "",
        "## Igualdade das contagens SQL com as contagens em memória",
        "",
        "Medidas dentro da transação, antes de `rls.sql` (depois do FORCE o dono lê 0 linhas).",
        "",
        _row("tabela", "memória", "SQL", "igual"),
        _row("---", "---:", "---:", "---"),
        *(
            _row(t, n, r.sql_counts.get(t), ok(r.sql_counts.get(t) == n))
            for t, n in sorted(r.memory_counts.items())
        ),
        "",
        "## Memória = oráculo independente",
        "",
        _row("verificação", "memória/SQL", "oráculo", "igual"),
        _row("---", "---:", "---:", "---"),
        *(_row(c.name, c.memory, c.oracle, ok(c.memory == c.oracle)) for c in r.checks),
        "",
        "## Protocolos por regra de ligação",
        "",
        _row("regra", "protocolos"),
        _row("---", "---:"),
        *(_row(k, v) for k, v in sorted(r.protocols_by_rule.items())),
        _row("sem vínculo", r.protocols_unlinked),
        "",
        "## Números do case",
        "",
        f"- `PROTOCOL_REF` que existem em protocolo: {r.protocol_ref[0]}/{r.protocol_ref[1]}",
        f"- Snapshot financeiro (oráculo): {r.snapshot[0]}/{r.snapshot[1]}",
        f"- Provas com nota encerradas sem tentativa: {r.lms.get('encerradas_sem_tentativa')}",
        f"- Simulados abertos sem tentativa: {r.lms.get('simulados_abertos')}",
        f"- Simulados abertos com tentativa restante: "
        f"{r.lms.get('simulados_com_tentativa_restante')}",
        f"- Logins de analista ausentes de toda tabela carregada: {r.logins_absent} "
        f"(presentes: {r.logins_present})",
        "",
        "## Resolução de identidade",
        "",
        _row("etapa", "sem resolução", "duplicados (fica o primeiro)"),
        _row("---", "---:", "---:"),
        *(_row(k, v["unresolved"], v["duplicates"]) for k, v in r.resolution.items()),
        "",
        "## Invariantes",
        "",
        *(f"- {k}: {ok(v)}" for k, v in r.invariants.items()),
        "",
        "## Cobertura temporal (início de cada fonte)",
        "",
        _row("fonte", "domínio", "desde"),
        _row("---", "---", "---"),
        *(_row(c["source"], c["domain"], c["starts_on"]) for c in r.coverage),
        "",
    ]
    return "\n".join(out)


def write_report(report: LoadReport, out: Path) -> None:
    """`data-quality.md` + `data-quality.json` em `out`."""
    (out / "data-quality.md").write_text(render_markdown(report), encoding="utf-8")
    (out / "data-quality.json").write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
