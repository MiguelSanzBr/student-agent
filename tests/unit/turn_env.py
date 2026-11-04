"""Ambiente comum dos testes do TurnService: Redis fake, LLM roteirizado, banco/cartão trocados."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

import app.service.turn as turn_mod
from app.agent.fakes import ScriptedLLM
from app.agent.llm import LLMResponse, Message, ToolChoice, Usage
from app.config import AppSettings
from app.data.card import StudentCard
from app.service.turn import DefaultTurnService
from app.tools.base import ToolSpec
from core.rkey import rkey
from core.scope import EnrollmentRef, StudentScope
from tests.unit.redis_fake import FakeRedis

SUB = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
MODEL = "gemini-3.8-flash"
SCOPE = StudentScope(
    SUB,
    (EnrollmentRef("1", UUID(int=7), "Administração - Centro", "UniSaoPaulo", "UniSaoPaulo",
                   "presencial", "ACTIVE", True),),
    ("UniSaoPaulo",),
)  # fmt: skip
OK = "Olá! Posso ajudar com suas finanças e com a sua vida acadêmica."


def R(text: str, tokens: int = 100) -> LLMResponse:
    return LLMResponse(text, (), Usage(input_tokens=tokens, output_tokens=10), MODEL)


class HangLLM:
    """Nunca responde: o turno só termina por cancelamento."""

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        import asyncio

        await asyncio.sleep(60)
        raise AssertionError("não chega aqui")


class FakeDb:
    @asynccontextmanager
    async def conn(self, *a: Any) -> AsyncIterator[None]:
        yield None


def patch_db(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(turn_mod, "with_scope", lambda pool, pk, fams: FakeDb().conn())

    async def card(conn: Any, scope: Any, settings: Any, *, today: Any, pending: Any = None) -> Any:
        return StudentCard("Cartão de teste", {})

    mp.setattr(turn_mod, "build_student_card", card)


def make_service(
    settings: AppSettings, llm: Any, redis: FakeRedis | None = None
) -> DefaultTurnService:
    return DefaultTurnService(settings, None, redis or FakeRedis(), llm)  # type: ignore[arg-type]


async def slots_in_use(svc: DefaultTurnService) -> int:
    return int(await svc.redis._r.zcard(rkey("slots", "global", "-", "pool")))  # type: ignore[attr-defined]


def settings_with(app_secrets: Path, **kw: Any) -> AppSettings:
    return AppSettings(**kw)


def scripted(*texts: str) -> ScriptedLLM:
    return ScriptedLLM([R(t) for t in texts])
