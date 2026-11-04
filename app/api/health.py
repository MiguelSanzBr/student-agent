from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/health")
async def health_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
