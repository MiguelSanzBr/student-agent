from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/")
async def web_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
