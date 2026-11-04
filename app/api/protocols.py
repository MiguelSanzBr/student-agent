from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/protocols")
async def protocols_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
