"""SQL do catálogo. Oferta, unidades e preços saem filtrados pela família (RLS + filtro)."""

from collections import defaultdict
from datetime import date
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection


async def _all(
    conn: AsyncConnection, sql: str, args: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    return await (await conn.execute(sql, args)).fetchall()


async def courses(conn: AsyncConnection) -> list[tuple[Any, ...]]:
    """(course_id, sigla, nome PT, grau)."""
    return await _all(
        conn, "SELECT course_id, acronym, name_pt, degree FROM canon.course ORDER BY name_pt"
    )


async def offers(conn: AsyncConnection, family: str, modality: str) -> dict[str, list[str]]:
    """course_id -> rótulos das unidades que oferecem o curso na modalidade (CAMPUS | DL)."""
    rows = await _all(
        conn,
        "SELECT o.course_id, u.label FROM canon.course_offer o"
        " JOIN canon.unit u ON u.unit_id = o.unit_id AND u.brand_family = o.brand_family"
        " WHERE o.brand_family = %s AND o.modality = %s ORDER BY u.label",
        (family, modality),
    )
    out: dict[str, list[str]] = defaultdict(list)
    for course_id, label in rows:
        if label not in out[course_id]:
            out[course_id].append(label)
    return out


async def prices(
    conn: AsyncConnection, family: str, modality: str, year: int
) -> dict[str, tuple[int, int, int]]:
    """course_id -> (mensalidade em centavos, ano, parcelas/semestre); o preço vigente no `year`."""
    rows = await _all(
        conn,
        "SELECT DISTINCT ON (course_id) course_id, mensalidade, ano, parcelas_por_semestre"
        " FROM canon.price WHERE brand_family = %s AND modality = %s AND ano <= %s"
        " ORDER BY course_id, ano DESC",
        (family, modality, year),
    )
    return {r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in rows}


async def curriculum_of(conn: AsyncConnection, enrollment_key: UUID) -> tuple[str, str]:
    """(curriculum_id, course_id) da matrícula."""
    rows = await _all(
        conn,
        "SELECT curriculum_id, course_id FROM canon.enrollment WHERE enrollment_key = %s",
        (enrollment_key,),
    )
    return rows[0]


async def subjects(conn: AsyncConnection, curriculum_id: str) -> list[tuple[Any, ...]]:
    """(código, nome PT, horas, créditos, semestre na matriz|None, obrigatória|None)."""
    return await _all(
        conn,
        "SELECT s.subject_code, s.name_pt, s.hours, s.credits, cs.semester, cs.mandatory"
        " FROM canon.subject s LEFT JOIN canon.curriculum_subject cs"
        "   ON cs.subject_code = s.subject_code AND cs.curriculum_id = %s"
        " ORDER BY s.name_pt",
        (curriculum_id,),
    )


async def terms(conn: AsyncConnection) -> list[tuple[str, date, date, date, date, bool]]:
    return await _all(
        conn,
        "SELECT term, start_date, end_date, av1_start, av2_start, is_current FROM canon.term"
        " ORDER BY start_date DESC",
    )


async def units(conn: AsyncConnection, family: str, brand: str) -> list[tuple[Any, ...]]:
    return await _all(
        conn,
        "SELECT label, unit_type, uf FROM canon.unit WHERE brand_family = %s AND brand = %s"
        " ORDER BY label",
        (family, brand),
    )
