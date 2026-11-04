"""Tokens de eval: roda dentro do container `evals` (segredos em /run/secrets/app)."""

from uuid import UUID

from app.api.auth import Aud, issue_token
from app.config import AppSettings


def mint(person_key: UUID, aud: Aud) -> str:
    return issue_token(person_key, aud, AppSettings())
