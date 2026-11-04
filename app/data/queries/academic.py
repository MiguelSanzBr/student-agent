"""Consultas acadêmicas (leitura sob RLS). Recebem a conexão da chamada e a chave da matrícula."""

from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

Row = dict[str, Any]

RESULT_FILTER = {
    "aprovado": ("APPROVED",),
    "reprovado": ("FAILED", "FAILED_ATTENDANCE"),
    "cursando": ("IN_PROGRESS",),
}


async def _all(conn: AsyncConnection, sql: str, params: tuple[Any, ...]) -> list[Row]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


async def enrollment(conn: AsyncConnection, ek: UUID) -> Row:
    rows = await _all(
        conn,
        "SELECT e.ra, e.status, e.status_date, e.unit_label, e.modality, e.shift, e.entry_type,"
        " e.enrolled_on, e.entry_term, e.semester, e.completion_pct, e.gpa, e.origin_brand,"
        " e.curriculum_id, e.course_id, c.name_pt AS course_name, c.duration_semesters"
        " FROM canon.enrollment e LEFT JOIN canon.course c USING (course_id)"
        " WHERE e.enrollment_key = %s",
        (ek,),
    )
    return rows[0]


async def events(conn: AsyncConnection, ek: UUID) -> list[Row]:
    return await _all(
        conn,
        "SELECT term, event_date, old_status, new_status, reason FROM canon.enrollment_event"
        " WHERE enrollment_key = %s ORDER BY event_date, new_status",
        (ek,),
    )


async def term_registrations(conn: AsyncConnection, ek: UUID) -> list[Row]:
    return await _all(
        conn,
        "SELECT term, term_status, registration_date FROM canon.term_enrollment"
        " WHERE enrollment_key = %s ORDER BY term DESC",
        (ek,),
    )


async def documents(conn: AsyncConnection, ek: UUID) -> list[Row]:
    return await _all(
        conn,
        "SELECT document_id, document_type, issued_at, delivery FROM canon.issued_document"
        " WHERE enrollment_key = %s ORDER BY issued_at, document_id",
        (ek,),
    )


async def current_term(conn: AsyncConnection) -> str:
    rows = await _all(conn, "SELECT term FROM canon.term WHERE is_current", ())
    return str(rows[0]["term"])


async def first_term_start(conn: AsyncConnection) -> tuple[str, date]:
    """Início da cobertura do SAG: o primeiro semestre do calendário."""
    rows = await _all(
        conn, "SELECT term, start_date FROM canon.term ORDER BY start_date LIMIT 1", ()
    )
    return str(rows[0]["term"]), rows[0]["start_date"]


async def results(
    conn: AsyncConnection, ek: UUID, term: str | None = None, result: str | None = None
) -> list[Row]:
    sql = (
        "SELECT subject_code, subject_name_pt, term, result, grade_av1, grade_av2, grade_av3,"
        " final_grade, attendance_pct, professor, divergencia FROM canon.class_result"
        " WHERE enrollment_key = %s"
    )
    params: list[Any] = [ek]
    if term:
        sql += " AND term = %s"
        params.append(term)
    if result:
        sql += " AND result = ANY(%s)"
        params.append(list(RESULT_FILTER[result]))
    return await _all(conn, sql + " ORDER BY term DESC, subject_code", tuple(params))


async def curriculum(conn: AsyncConnection, curriculum_id: str) -> list[Row]:
    return await _all(
        conn,
        "SELECT cs.subject_code, s.name_pt, s.hours, cs.semester, cs.mandatory,"
        " cs.prerequisite_code, p.name_pt AS prerequisite_name"
        " FROM canon.curriculum_subject cs JOIN canon.subject s USING (subject_code)"
        " LEFT JOIN canon.subject p ON p.subject_code = cs.prerequisite_code"
        " WHERE cs.curriculum_id = %s ORDER BY cs.semester, cs.subject_code",
        (curriculum_id,),
    )


async def curriculum_hours(conn: AsyncConnection, curriculum_id: str) -> Any:
    rows = await _all(
        conn, "SELECT total_hours FROM canon.curriculum WHERE curriculum_id = %s", (curriculum_id,)
    )
    return rows[0]["total_hours"] if rows else None
