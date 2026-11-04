from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.post("/feedback")
async def feedback_stub() -> None:
    raise HTTPException(status_code=501, detail="não implementado")
