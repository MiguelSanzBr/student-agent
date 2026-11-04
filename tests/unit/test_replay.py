import inspect
from datetime import date
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from app.agent import make_llm
from app.agent.fakes import ScriptedLLM
from app.agent.gemini import GeminiProvider
from app.agent.graph import run_turn
from app.agent.llm import LLMResponse, Message, ToolCall, Usage
from app.agent.replay import RecordingLLM, ReplayLLM
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
    got = ReplayLLM(path).suggested()
    assert "Pergunta A" in got and "Pergunta B" in got
    assert got.index("Pergunta A") < got.index("Pergunta B")


async def test_auto_without_key_uses_replay(app_secrets: object) -> None:
    s = AppSettings(llm_provider="auto", gemini_api_key=None)
    llm = make_llm(s)
    assert isinstance(llm, ReplayLLM)
    assert llm.suggested()  # o roteiro versionado tem perguntas sugeridas


async def test_auto_with_key_uses_gemini(app_secrets: object) -> None:
    s = AppSettings(llm_provider="auto", gemini_api_key="k" * 32)
    assert isinstance(make_llm(s), GeminiProvider)


async def test_auto_with_other_keys_uses_their_provider(app_secrets: object) -> None:
    from app.agent.claude import AnthropicProvider
    from app.agent.gpt import OpenAIProvider

    s = AppSettings(llm_provider="auto", anthropic_api_key="k" * 32)
    assert isinstance(make_llm(s), AnthropicProvider)
    s = AppSettings(llm_provider="auto", openai_api_key="k" * 32)
    assert isinstance(make_llm(s), OpenAIProvider)


async def test_replay_provider_ships_recordings(app_secrets: object) -> None:
    s = AppSettings(llm_provider="replay")
    assert isinstance(make_llm(s), ReplayLLM)


async def test_hedge_lever_keeps_demo_suggestions(app_secrets: object) -> None:
    # o hedge mora no TurnService (Task 33): make_llm nunca embrulha o provider
    rep = make_llm(AppSettings(llm_provider="replay", lever_hedge_first_call=True))
    assert isinstance(rep, ReplayLLM) and callable(rep.suggested)  # chips e faixa preservados


async def test_every_replay_response_is_labelled_demo_replay(tmp_path: Path) -> None:
    path = tmp_path / "rec.jsonl"
    rec = RecordingLLM(ScriptedLLM([R("gravada")]), path)
    await rec.generate(
        model="m", system="s", messages=[Message("user", "Pergunta A")], tools=[], deadline=1.0
    )
    replay = ReplayLLM(path)
    for q in ("Pergunta A", "quanto eu devo?", "pergunta fora do roteiro"):
        out = await replay.generate(
            model="gemini-3.8-flash",
            system="s",
            messages=[Message("user", q)],
            tools=[],
            deadline=1.0,
        )
        assert out.model == "demo-replay", q


async def test_replay_ignores_guard_feedback_when_finding_the_question(tmp_path: Path) -> None:
    from app.agent import graph

    prefix = "Sua resposta foi reprovada na conferência"
    assert prefix in inspect.getsource(graph)  # o marcador que o grafo usa
    path = tmp_path / "rec.jsonl"
    await RecordingLLM(ScriptedLLM([R("Resposta gravada.")]), path).generate(
        model="m", system="s", messages=[Message("user", "quanto devo?")], tools=[], deadline=1.0
    )
    msgs = [
        Message("user", "quanto devo?"),
        Message("assistant", "Resposta recusada."),
        Message("user", prefix + " (valor). Reescreva."),
    ]
    out = await ReplayLLM(path).generate(
        model="m", system="s", messages=msgs, tools=[], deadline=1.0
    )
    assert out.text == "Resposta gravada."
