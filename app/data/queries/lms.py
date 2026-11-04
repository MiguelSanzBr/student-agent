"""SQL do LMS. Tudo sob RLS (a conexão já vem com app.person_key)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

_LOGIN = "\\core\\event\\user_loggedin"
_SP_DATE = "(occurred_at AT TIME ZONE 'America/Sao_Paulo')::date"


async def _all(conn: AsyncConnection, sql: str, args: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    return await (await conn.execute(sql, args)).fetchall()


async def last_access(conn: AsyncConnection, ek: UUID) -> tuple[int, datetime | None]:
    """(nº de contas AVA da matrícula, último acesso entre elas)."""
    rows = await _all(
        conn, "SELECT count(*), max(last_access) FROM canon.lms_account WHERE enrollment_key = %s",
        (ek,),
    )  # fmt: skip
    return rows[0][0], rows[0][1]


async def logins(conn: AsyncConnection, ek: UUID, start: date, end: date) -> int:
    rows = await _all(
        conn,
        f"SELECT count(*) FROM canon.lms_event WHERE enrollment_key = %s AND event_name = %s"
        f" AND {_SP_DATE} BETWEEN %s AND %s",
        (ek, _LOGIN, start, end),
    )
    return int(rows[0][0])


async def rooms(conn: AsyncConnection, ek: UUID) -> list[str]:
    """Disciplinas (PT) de toda sala em que houve evento; salas sem tipificação ficam de fora."""
    rows = await _all(
        conn,
        "SELECT DISTINCT subject_name_pt FROM canon.lms_event"
        " WHERE enrollment_key = %s AND subject_name_pt IS NOT NULL ORDER BY 1",
        (ek,),
    )
    return [r[0] for r in rows]


async def quizzes(conn: AsyncConnection, ek: UUID) -> list[tuple[Any, ...]]:
    return await _all(
        conn,
        "SELECT quiz_id, quiz_type, opens_at, closes_at, attempts_allowed, subject_name_pt"
        " FROM canon.lms_quiz WHERE enrollment_key = %s",
        (ek,),
    )


async def attempts(conn: AsyncConnection, ek: UUID) -> list[str]:
    """Um item por tentativa (a abandonada também consome tentativa): o quiz_id."""
    rows = await _all(
        conn, "SELECT quiz_id FROM canon.lms_quiz_attempt WHERE enrollment_key = %s", (ek,)
    )
    return [r[0] for r in rows]


async def lms_coverage(conn: AsyncConnection) -> date:
    rows = await _all(conn, "SELECT starts_on FROM canon.coverage WHERE domain = 'lms'", ())
    since: date = rows[0][0]
    return since


async def term_window(conn: AsyncConnection, term: str | None) -> tuple[str, date, date] | None:
    """Período (`term` ou, sem ele, o vigente) com início e fim; None se não existe."""
    sql = "SELECT term, start_date, end_date FROM canon.term WHERE "
    rows = await _all(conn, sql + ("term = %s" if term else "is_current"), (term,) if term else ())
    return rows[0] if rows else None


async def calendar(conn: AsyncConnection) -> dict[str, date]:
    rows = await _all(conn, "SELECT av1_start, av2_start FROM canon.term WHERE is_current", ())
    return {"AV1": rows[0][0], "AV2": rows[0][1]} if rows else {}
