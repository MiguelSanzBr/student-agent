from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/a2a")
async def a2a_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
