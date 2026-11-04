"""Conexão por chamada de ferramenta, sob RLS. Nunca segurar a conexão durante o await do LLM."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool

from app.config import AppSettings
from app.infra.stores.labels import LabelCache
from app.obs.tracing import span
from core.clock import wall_now
from core.keys import brand_family
from core.scope import EnrollmentRef, StudentScope

Pool = AsyncConnectionPool[AsyncConnection]


async def _reset(conn: AsyncConnection) -> None:
    # autocommit: DISCARD ALL não roda dentro de transação
    await conn.execute("RESET ALL")
    await conn.execute("DISCARD ALL")


async def _configure(conn: AsyncConnection) -> None:
    await conn.set_autocommit(True)
    conn.prepare_threshold = None  # DISCARD ALL no reset derruba os prepared statements


def conninfo(settings: AppSettings) -> str:
    return make_conninfo(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user="agent_app", password=settings.agent_app_password,
    )  # fmt: skip


def create_pool(settings: AppSettings, *, max_size: int = 10) -> Pool:
    """Pool fechado; o chamador faz `await pool.open()`."""
    return AsyncConnectionPool(
        conninfo=conninfo(settings), min_size=1, max_size=max_size, open=False,
        configure=_configure, reset=_reset, connection_class=AsyncConnection,
    )  # fmt: skip


async def assert_safe_role(conn: AsyncConnection) -> None:
    cur = await conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )
    row = await cur.fetchone()
    if row is None or row[0] or row[1]:
        raise RuntimeError("a role da API é privilegiada (SUPERUSER ou BYPASSRLS)")


def _array_literal(items: tuple[str, ...]) -> str:
    def q(i: str) -> str:  # só cita o que o literal de array exige
        return i if i.isalnum() else '"' + i.replace("\\", "\\\\").replace('"', '\\"') + '"'

    return "{" + ",".join(q(i) for i in items) + "}"


@asynccontextmanager
async def with_scope(
    pool: Pool, person_key: UUID, families: tuple[str, ...]
) -> AsyncIterator[AsyncConnection]:
    """Uma conexão por chamada; app.* valem só nesta transação (set_config local)."""
    # span `db.query`: uma conexão/transação por chamada; sem SQL com valores nem person_key
    with span("db.query", "CHAIN", **{"db.system": "postgresql", "db.families": len(families)}):
        async with pool.connection() as conn, conn.transaction():
            await conn.execute(
                "SELECT set_config('app.person_key', %s, true), set_config('app.brands', %s, true),"
                " set_config('app.now', %s, true)",
                (str(person_key), _array_literal(families), wall_now().isoformat()),
            )
            yield conn


async def _course_label(conn: AsyncConnection, labels: LabelCache, course_id: str) -> str:
    key = f"course:{course_id}"
    if (hit := await labels.get(key)) is not None:
        return hit
    cur = await conn.execute("SELECT name_pt FROM canon.course WHERE course_id = %s", (course_id,))
    row = await cur.fetchone()
    name = row[0] if row else course_id
    await labels.put(key, name)
    return str(name)


async def load_scope(conn: AsyncConnection, person_key: UUID, labels: LabelCache) -> StudentScope:
    """Lê as matrículas sob RLS (a 1ª chamada usa families=(): enrollment só filtra por pessoa).

    Ordem = índice: ACTIVE antes, depois a mais recente; a 1ª é a principal. Máx. 3.
    """
    cur = await conn.execute(
        "SELECT enrollment_key, brand, course_id, unit_label, modality, status"
        " FROM canon.enrollment"
        " ORDER BY (status <> 'ACTIVE'), enrolled_on DESC NULLS LAST, enrollment_key LIMIT 3"
    )
    refs = []
    for i, (ek, brand, course_id, unit, modality, status) in enumerate(await cur.fetchall()):
        label = await _course_label(conn, labels, course_id)
        refs.append(
            EnrollmentRef(
                str(i + 1),  # type: ignore[arg-type]
                ek, f"{label} - {unit}" if unit else label, brand, brand_family(brand),
                "ead" if str(modality).upper() == "EAD" else "presencial", status, i == 0,
            )
        )  # fmt: skip
    return StudentScope(person_key, tuple(refs), tuple(dict.fromkeys(r.brand_family for r in refs)))
