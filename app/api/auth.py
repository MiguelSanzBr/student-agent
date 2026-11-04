"""JWT HS256 por canal: `aud=chat` no cookie; `aud=a2a` em Bearer ou `x-api-key` (Langflow)."""

from datetime import timedelta
from typing import Literal
from uuid import UUID

import jwt
from fastapi import HTTPException, Request

from app.config import AppSettings
from core.clock import wall_now

Aud = Literal["chat", "a2a"]
COOKIE = "session"


class InvalidToken(Exception):
    """Mensagem fixa: nunca repete o token."""


def issue_token(person_key: UUID, aud: Aud, settings: AppSettings) -> str:
    now = wall_now()
    claims = {
        "sub": str(person_key), "aud": aud, "iat": now,
        "exp": now + timedelta(seconds=settings.limits.jwt_ttl_s),
    }  # fmt: skip
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def verify_token(token: str, aud: Aud, settings: AppSettings) -> UUID:
    try:
        claims = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], audience=aud,
            options={"require": ["exp", "sub", "aud"]},
        )  # fmt: skip
        return UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise InvalidToken("token inválido") from None


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="não autenticado")


def _verify(token: str | None, aud: Aud, request: Request) -> UUID:
    if not token:
        raise _unauthorized()
    try:
        return verify_token(token, aud, request.app.state.settings)
    except InvalidToken:
        raise _unauthorized() from None


def get_person_cookie(request: Request) -> UUID:
    return _verify(request.cookies.get(COOKIE), "chat", request)


def get_person_a2a(request: Request) -> UUID:
    auth = request.headers.get("authorization", "")
    scheme, _, bearer = auth.partition(" ")
    token = bearer.strip() if scheme.lower() == "bearer" else request.headers.get("x-api-key")
    return _verify(token, "a2a", request)
