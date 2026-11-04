from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/metrics")
async def metrics_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
