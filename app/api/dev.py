"""Login de desenvolvimento: só com `dev_login=True` e só para os casos golden."""

import hmac
import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.auth import COOKIE, issue_token
from app.config import AppSettings
from app.infra.stores.tickets import LoginTickets

router = APIRouter()


class DevCredentials(BaseModel):
    secret: str
    case: str


def load_golden(path: Path) -> dict[str, UUID]:
    """`golden_login.json` (Task 13): {cases: [{case, person_key}]}; sem CPF."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise FileNotFoundError(
            f"{path} não existe: rode o ETL: docker compose run --rm etl"
        ) from None
    return {str(c["case"]): UUID(c["person_key"]) for c in data["cases"]}


def _golden_path(request: Request) -> Path:
    path: Path | None = getattr(request.app.state, "golden_path", None)
    return path or request.app.state.settings.etl_out_dir / "golden_login.json"


def _enabled(request: Request) -> AppSettings:
    s: AppSettings = request.app.state.settings
    if not s.dev_login:
        raise HTTPException(status_code=404)
    return s


def _golden_person(request: Request, creds: DevCredentials, s: AppSettings) -> UUID:
    ok = hmac.compare_digest(creds.secret.encode(), s.dev_login_secret.encode())
    pk = load_golden(_golden_path(request)).get(creds.case)
    if not ok or pk is None:  # mesma resposta: não revela qual falhou
        raise HTTPException(status_code=403, detail="negado")
    return pk


def _set_cookie(resp: Response, pk: UUID, s: AppSettings) -> None:
    resp.set_cookie(
        COOKIE, issue_token(pk, "chat", s), max_age=s.limits.jwt_ttl_s, path="/",
        httponly=True, samesite="strict", secure=s.cookie_secure,
    )  # fmt: skip


@router.get("/dev/login")
async def dev_login_ticket(request: Request, ticket: str) -> RedirectResponse:
    s = _enabled(request)
    pk = await LoginTickets(request.app.state.redis, s.limits).consume(ticket)
    if pk is None or pk not in load_golden(_golden_path(request)).values():
        raise HTTPException(status_code=403, detail="negado")
    resp = RedirectResponse("/", status_code=303)
    _set_cookie(resp, pk, s)
    return resp


@router.post("/dev/login")
async def dev_login_secret(
    request: Request, creds: DevCredentials, response: Response
) -> dict[str, str]:
    s = _enabled(request)
    _set_cookie(response, _golden_person(request, creds, s), s)
    return {"status": "ok"}


@router.post("/dev/a2a-token")
async def dev_a2a_token(request: Request, creds: DevCredentials) -> dict[str, str]:
    s = _enabled(request)
    return {"token": issue_token(_golden_person(request, creds, s), "a2a", s)}
