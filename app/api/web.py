"""Chat web estático: só rotas fixas (sem StaticFiles, logo sem path traversal), com CSP estrita."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()
WEB = Path(__file__).resolve().parents[1] / "web"
HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
}


def _file(name: str) -> FileResponse:
    if name not in FILES:
        raise HTTPException(status_code=404)
    return FileResponse(WEB / name, media_type=FILES[name], headers=HEADERS)


@router.get("/")
async def index() -> FileResponse:
    return _file("index.html")


@router.get("/static/{name}")
async def static(name: str) -> FileResponse:
    return _file(name)
