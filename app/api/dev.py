from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/dev/login")
async def dev_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
