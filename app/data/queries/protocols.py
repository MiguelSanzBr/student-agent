"""Consultas de protocolos e fatos de elegibilidade; a conexão já traz o escopo (RLS)."""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

OPEN_STATUSES = ("Open", "In Analysis", "Pending Requester")


async def _all(conn: AsyncConnection, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    return await (await conn.execute(sql, args)).fetchall()


async def list_secretary(conn: AsyncConnection) -> list[tuple[Any, ...]]:
    """(protocolo, serviço, status, abertura, papel do solicitante)."""
    return await _all(
        conn,
        "SELECT protocol, service_code, status, opened_at, solicitante_papel"
        " FROM canon.protocol ORDER BY protocol",
    )


async def list_sa(conn: AsyncConnection) -> list[tuple[Any, ...]]:
    """(id, serviço, aberto em, prazo)."""
    return await _all(
        conn, "SELECT id, service_code, opened_on, prazo FROM agent_rw.protocol_request ORDER BY id"
    )


async def secretary_protocol(conn: AsyncConnection, numero: str) -> tuple[Any, ...] | None:
    rows = await _all(
        conn,
        "SELECT protocol, service_code, status, opened_at, solicitante_papel"
        " FROM canon.protocol WHERE protocol = %s",
        numero,
    )
    return rows[0] if rows else None


async def sa_protocol(conn: AsyncConnection, numero: str) -> tuple[Any, ...] | None:
    rows = await _all(
        conn,
        "SELECT id, service_code, opened_on, prazo FROM agent_rw.protocol_request WHERE id = %s",
        numero,
    )
    return rows[0] if rows else None


async def events(conn: AsyncConnection, numero: str) -> list[tuple[Any, ...]]:
    return await _all(
        conn,
        "SELECT from_status, to_status, changed_at FROM canon.protocol_event"
        " WHERE protocol = %s ORDER BY seq",
        numero,
    )


async def messages(conn: AsyncConnection, numero: str) -> list[tuple[Any, ...]]:
    """(autor, modelo, texto_livre, criada em); `texto_livre` só para o chamador descartar."""
    return await _all(
        conn,
        "SELECT autor_papel, modelo, texto_livre, created_at FROM canon.protocol_message"
        " WHERE protocol = %s ORDER BY created_at, message_id",
        numero,
    )


async def documents(conn: AsyncConnection, numero: str) -> list[tuple[Any, ...]]:
    """Sem `codigo_validacao` (segredo do portador) nem document_id."""
    return await _all(
        conn,
        "SELECT document_type, issued_at, delivery FROM canon.issued_document"
        " WHERE protocol = %s ORDER BY issued_at",
        numero,
    )


async def linked_invoice(conn: AsyncConnection, numero: str) -> tuple[Any, ...] | None:
    rows = await _all(
        conn,
        "SELECT invoice_id, competence, status, due_date, net_cents FROM canon.invoice"
        " WHERE protocol_ref = %s ORDER BY invoice_id LIMIT 1",
        numero,
    )
    return rows[0] if rows else None


async def coverage_start(conn: AsyncConnection, domain: str) -> date | None:
    rows = await _all(conn, "SELECT starts_on FROM canon.coverage WHERE domain = %s", domain)
    return rows[0][0] if rows else None


async def class_results(conn: AsyncConnection, enrollment_key: UUID) -> list[tuple[Any, ...]]:
    """(código, nome PT, período, resultado, há nota lançada)."""
    return await _all(
        conn,
        "SELECT subject_code, subject_name_pt, term, result,"
        " (grade_av1 IS NOT NULL OR grade_av2 IS NOT NULL OR grade_av3 IS NOT NULL"
        "  OR final_grade IS NOT NULL) FROM canon.class_result WHERE enrollment_key = %s",
        enrollment_key,
    )


async def current_term(conn: AsyncConnection) -> str | None:
    rows = await _all(conn, "SELECT term FROM canon.term WHERE is_current LIMIT 1")
    return rows[0][0] if rows else None


async def diploma_issued(conn: AsyncConnection, enrollment_key: UUID) -> bool:
    rows = await _all(
        conn,
        "SELECT 1 FROM canon.issued_document WHERE enrollment_key = %s"
        " AND document_type = 'Diploma' LIMIT 1",
        enrollment_key,
    )
    return bool(rows)


async def invoices(conn: AsyncConnection, enrollment_key: UUID) -> list[tuple[Any, ...]]:
    return await _all(
        conn,
        "SELECT invoice_id, net_cents, status, due_date FROM canon.invoice"
        " WHERE enrollment_key = %s",
        enrollment_key,
    )


async def payments(conn: AsyncConnection, enrollment_key: UUID) -> list[tuple[Any, ...]]:
    return await _all(
        conn,
        "SELECT invoice_id, principal_cents, status FROM canon.payment WHERE enrollment_key = %s",
        enrollment_key,
    )


async def open_secretary(conn: AsyncConnection, enrollment_key: UUID) -> list[tuple[str, datetime]]:
    return await _all(
        conn,
        "SELECT service_code, opened_at FROM canon.protocol"
        " WHERE enrollment_key = %s AND status = ANY(%s)",
        enrollment_key,
        list(OPEN_STATUSES),
    )


async def open_sa(conn: AsyncConnection, enrollment_index: str) -> list[tuple[Any, ...]]:
    return await _all(
        conn,
        "SELECT service_code, subject_code, opened_on FROM agent_rw.protocol_request"
        " WHERE enrollment_index = %s",
        enrollment_index,
    )
