from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    st = request.app.state
    db = redis = True
    try:
        async with st.pool.connection() as conn:
            await conn.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - qualquer falha é "indisponível"; detalhe não vaza
        db = False
    try:
        await st.redis.ping()
    except Exception:  # noqa: BLE001
        redis = False
    ok = db and redis
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "db": db, "redis": redis},
        status_code=200 if ok else 503,
    )
