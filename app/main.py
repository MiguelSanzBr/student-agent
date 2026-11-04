import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from pydantic import ValidationError

from app.agent import make_llm
from app.api import ROUTERS
from app.config import AppSettings
from app.data.db import Pool, assert_safe_role, create_pool
from app.infra.redis import RealRedis
from app.obs import annotations
from app.obs.logging import configure_logging
from app.obs.tracing import setup_tracing
from app.service.turn import DefaultTurnService


async def startup_checks(pool: Pool, redis: RealRedis) -> None:
    """Aborta se a role da API for privilegiada ou se o Redis (com senha) não responder."""
    async with pool.connection() as conn:
        await assert_safe_role(conn)
    await redis.ping()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        settings = AppSettings()
    except ValidationError as e:  # o pydantic ecoaria o valor do segredo: só os nomes dos campos
        campos = ", ".join(sorted({str(x["loc"][0]) for x in e.errors() if x["loc"]}))
        raise RuntimeError(
            f"configuração inválida (segredo ausente, curto ou de exemplo): {campos}"
        ) from None
    configure_logging(settings)
    pool = create_pool(settings)
    redis = RealRedis(settings.redis_host, settings.redis_port, settings.redis_password)
    tracing = None
    try:
        tracing = setup_tracing(settings)
        await pool.open(wait=True)
        await startup_checks(pool, redis)
        app.state.settings, app.state.pool, app.state.redis = settings, pool, redis
        app.state.turns = DefaultTurnService(settings, pool, redis, make_llm(settings))
        yield
    finally:
        for step in (redis.aclose, pool.close, annotations.drain):
            with suppress(Exception):
                await step()
        if tracing is not None:
            with suppress(Exception):
                await asyncio.to_thread(tracing.shutdown)  # esvazia o BatchSpanProcessor


app = FastAPI(title="student-agent", lifespan=lifespan)
for _router in ROUTERS:
    app.include_router(_router)
