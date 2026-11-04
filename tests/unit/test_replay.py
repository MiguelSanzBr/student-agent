from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from app.agent import make_llm
from app.agent.fakes import ScriptedLLM
from app.agent.gemini import GeminiProvider
from app.agent.graph import run_turn
from app.agent.llm import LLMResponse, Message, ToolCall, Usage
from app.agent.replay import DEFAULT_REPLAY, UNKNOWN_TEXT, RecordingLLM, ReplayLLM
from app.config import AppSettings
from app.data.card import StudentCard
from app.infra.stores.history import Conversation
from app.tools.base import ToolContext
from core.clock import monotonic
from core.scope import EnrollmentRef, StudentScope

CARD = StudentCard("Cartão do aluno: matrícula 1.", {})
EMPTY_CONV = Conversation((), False, False, False)


def _scope() -> StudentScope:
    enr = EnrollmentRef("1", uuid4(), "Curso 1", "UniSaoPaulo", "UniSaoPaulo",
                        "presencial", "ACTIVE", True)  # fmt: skip
    return StudentScope(uuid4(), (enr,), ("UniSaoPaulo",))


def R(text: str | None = None, *calls: ToolCall) -> LLMResponse:
    return LLMResponse(text, calls, Usage(10, 5), "m")


async def _answer(llm: Any, text: str) -> str:
    scope = _scope()
    st = AppSettings()

    def factory(rnd: int, tainted: bool) -> ToolContext:
        return ToolContext(scope, st, cast(Any, "web"), "conv", tainted, rnd, True,
                           date(2026, 10, 31),
                           cast(Any, None), cast(Any, None))  # fmt: skip

    r = await run_turn(
        llm=llm, settings=st, scope=scope, card=CARD, conversation=EMPTY_CONV, text=text,
        channel="web", ctx_factory=factory, deadline=monotonic() + 30, writer=lambda _: None,
        model="m", tools=(),
    )  # fmt: skip
    return r.answer


async def test_record_then_replay(app_secrets: object, tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    rec = RecordingLLM(ScriptedLLM([R("Posso ajudar com a sua situação financeira.")]), path)
    recorded = await _answer(rec, "quanto devo?")
    replayed = await _answer(ReplayLLM(path), "quanto devo?")
    assert recorded == replayed == "Posso ajudar com a sua situação financeira."


async def test_record_then_replay_multi_step(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    call = ToolCall("c1", "get_student_card", {})
    inner = ScriptedLLM([R(None, call), R("Pronto.")])
    rec = RecordingLLM(inner, path)
    msgs = [Message("user", "oi")]
    first = await rec.generate(model="m", system="s", messages=msgs, tools=[], deadline=1.0)
    msgs += [
        Message("assistant", tool_calls=first.tool_calls),
        Message("tool", tool_call_id="c1", tool_name="get_student_card", tool_result={}),
    ]
    second = await rec.generate(model="m", system="s", messages=msgs, tools=[], deadline=1.0)
    replay = ReplayLLM(path)
    r1 = await replay.generate(
        model="m", system="s", messages=[Message("user", "oi")], tools=[], deadline=1.0
    )
    r2 = await replay.generate(model="m", system="s", messages=msgs, tools=[], deadline=1.0)
    assert r1.tool_calls == first.tool_calls and r1.text is None
    assert r2.text == second.text == "Pronto."


async def test_unknown_question_message(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    path.write_text("")
    out = await ReplayLLM(path).generate(
        model="m", system="s", messages=[Message("user", "pergunta nova")], tools=[], deadline=1.0
    )
    assert out.text == "Modo demonstração: escolha uma das perguntas sugeridas."


async def test_suggested_lists_recorded_questions(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    rec = RecordingLLM(ScriptedLLM([R("a"), R("b")]), path)
    await rec.generate(
        model="m", system="s", messages=[Message("user", "Pergunta A")], tools=[], deadline=1.0
    )
    await rec.generate(
        model="m", system="s", messages=[Message("user", "Pergunta B")], tools=[], deadline=1.0
    )
    assert ReplayLLM(path).suggested() == ["Pergunta A", "Pergunta B"]


async def test_auto_without_key_uses_replay(app_secrets: object) -> None:
    s = AppSettings(llm_provider="auto", gemini_api_key=None)
    llm = make_llm(s)
    assert isinstance(llm, ReplayLLM)
    assert s.demo_mode is True
    assert llm.suggested()  # o roteiro versionado tem perguntas sugeridas


async def test_auto_with_key_uses_gemini(app_secrets: object) -> None:
    s = AppSettings(llm_provider="auto", gemini_api_key="k" * 32)
    assert isinstance(make_llm(s), GeminiProvider)
    assert s.demo_mode is False


async def test_replay_provider_ships_recordings(app_secrets: object) -> None:
    s = AppSettings(llm_provider="replay")
    assert isinstance(make_llm(s), ReplayLLM)
    assert s.demo_mode is True


async def test_shipped_recordings_answer_every_suggested_question() -> None:
    llm = ReplayLLM(DEFAULT_REPLAY)
    for q in llm.suggested():
        out = await llm.generate(
            model="m", system="s", messages=[Message("user", q)], tools=[], deadline=1.0
        )
        assert out.text and out.text != UNKNOWN_TEXT, q
