from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/chat")
async def chat_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
