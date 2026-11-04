from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import ValidationError

from app.api import ROUTERS
from app.config import AppSettings
from app.data.db import Pool, assert_safe_role, create_pool
from app.infra.redis import RealRedis


async def startup_checks(settings: AppSettings, pool: Pool, redis: RealRedis) -> None:
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
    pool = create_pool(settings)
    redis = RealRedis(settings.redis_host, settings.redis_port, settings.redis_password)
    await pool.open(wait=True)
    try:
        await startup_checks(settings, pool, redis)
        app.state.settings, app.state.pool, app.state.redis = settings, pool, redis
        yield
    finally:
        await redis.aclose()
        await pool.close()


app = FastAPI(title="student-agent", lifespan=lifespan)
for _router in ROUTERS:
    app.include_router(_router)
