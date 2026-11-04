import asyncio
import json
import time
from collections.abc import AsyncIterator
from uuid import UUID

import psycopg
import pytest

from app.config import AppSettings
from app.data.db import assert_safe_role, create_pool, load_scope, with_scope
from app.infra.stores.labels import LabelCache
from app.tools.base import ToolError, resolve_enrollment
from tests.conftest import GOOD
from tests.integration.conftest import Loaded
from tests.unit.redis_fake import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _settings(loaded: Loaded) -> AppSettings:
    pw = (loaded.db.secrets / "agent_app_password").read_text().strip()
    return AppSettings(
        **{**GOOD, "agent_app_password": pw},  # type: ignore[arg-type]
        db_host="127.0.0.1", db_port=int(loaded.db.port), db_name=loaded.db.name,
    )  # fmt: skip


def _person(loaded: Loaded, case: int) -> UUID:
    rows = json.loads((loaded.out / "golden_login.json").read_text())["cases"]
    return UUID(next(r["person_key"] for r in rows if r["case"] == case))


@pytest.fixture
async def pool(loaded_db: Loaded) -> AsyncIterator:  # type: ignore[type-arg]
    p = create_pool(_settings(loaded_db))
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _scope(pool, pk: UUID):  # type: ignore[no-untyped-def]
    async with with_scope(pool, pk, ()) as c:
        return await load_scope(c, pk, LabelCache(FakeRedis()))


async def test_load_scope_golden9_and_rls(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    pk = _person(loaded_db, 9)
    scope = await _scope(pool, pk)
    assert scope.person_key == pk and len(scope.enrollments) == 2
    assert [e.index for e in scope.enrollments] == ["1", "2"]
    assert scope.enrollments[0].is_primary and not scope.enrollments[1].is_primary
    assert set(scope.brand_families) == {"UniSaoPaulo"}
    assert all(e.label for e in scope.enrollments)
    # RLS: outro aluno não aparece sob a mesma chamada
    other = _person(loaded_db, 1)
    async with with_scope(pool, pk, scope.brand_families) as c:
        cur = await c.execute(
            "SELECT count(*) FROM canon.enrollment WHERE person_key = %s",
            (other,),
        )
        assert (await cur.fetchone())[0] == 0  # type: ignore[index]
        cur = await c.execute("SELECT current_setting('app.now')")
        assert (await cur.fetchone())[0]  # type: ignore[index]


async def test_prefer_status_golden9(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    scope = await _scope(pool, _person(loaded_db, 9))
    with loaded_db.db.connect("postgres") as c:
        key = c.execute(
            "SELECT enrollment_key FROM canon.enrollment WHERE ra = '011843'"
        ).fetchone()
    assert key is not None
    ref = resolve_enrollment(scope, None, "GRADUATED")
    assert ref.enrollment_key == key[0] and ref.status == "GRADUATED"
    with pytest.raises(ToolError):
        resolve_enrollment(await _scope(pool, _person(loaded_db, 1)), "3", None)


async def test_pool_reset(pool) -> None:  # type: ignore[no-untyped-def]
    async with pool.connection() as c:
        await c.execute("SET app.person_key = 'vazou'")
    async with pool.connection() as c:
        cur = await c.execute("SELECT current_setting('app.person_key', true)")
        assert (await cur.fetchone())[0] in (None, "")  # type: ignore[index]


async def test_scope_is_per_transaction(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    pk = _person(loaded_db, 1)
    async with with_scope(pool, pk, ("UniSaoPaulo",)) as c:
        cur = await c.execute("SELECT current_setting('app.brands')")
        assert (await cur.fetchone())[0] == "{UniSaoPaulo}"  # type: ignore[index]
    async with pool.connection() as c:
        cur = await c.execute("SELECT util.current_person()")
        assert (await cur.fetchone())[0] is None  # type: ignore[index]


async def test_pool1_two_concurrent_turns_no_deadlock(loaded_db: Loaded) -> None:
    p = create_pool(_settings(loaded_db), max_size=1)
    await p.open()
    try:
        pks = [_person(loaded_db, 1), _person(loaded_db, 9)]

        async def turn(pk: UUID) -> None:
            for _ in range(2):
                async with with_scope(p, pk, ()) as c:
                    await c.execute("SELECT 1")
                await asyncio.sleep(0.2)  # o "LLM": sem conexão segurada

        t0 = time.monotonic()
        await asyncio.wait_for(asyncio.gather(*(turn(k) for k in pks)), 2)
        assert time.monotonic() - t0 < 2
    finally:
        await p.close()


async def test_assert_safe_role(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    async with pool.connection() as c:
        await assert_safe_role(c)  # agent_app: ok
    pw = (loaded_db.db.secrets / "postgres_password").read_text().strip()
    async with await psycopg.AsyncConnection.connect(
        host="127.0.0.1", port=int(loaded_db.db.port), dbname=loaded_db.db.name,
        user="postgres", password=pw,
    ) as su:  # fmt: skip
        with pytest.raises(RuntimeError):
            await assert_safe_role(su)
