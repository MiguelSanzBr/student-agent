import asyncio
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest
from google.genai import errors, types

import app.agent.gemini as gm
from app.agent import make_llm
from app.agent.fakes import ScriptedLLM
from app.agent.gemini import GeminiProvider
from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall, Usage
from app.config import AppSettings
from app.tools.base import ToolSpec
from core.clock import monotonic

SPEC = ToolSpec("get_x", "d", {"type": "object", "properties": {}, "additionalProperties": False})
M1, M2 = "gemini-3.5-flash-lite", "gemini-3.8-flash"


def ok_response(parts: list[types.Part] | None = None) -> Any:
    parts = parts or [types.Part(text="oi")]
    return NS(
        candidates=[NS(content=types.Content(role="model", parts=parts),
                       finish_reason=types.FinishReason.STOP)],
        usage_metadata=NS(prompt_token_count=10, candidates_token_count=3,
                          thoughts_token_count=2, cached_content_token_count=4),
    )  # fmt: skip


class FakeModels:
    def __init__(self, outcomes: list[Any] | None = None, delay: float = 0) -> None:
        self.outcomes = outcomes or []
        self.calls: list[dict[str, Any]] = []
        self.running = 0
        self.max_running = 0
        self.delay = delay

    async def generate_content(self, **kw: Any) -> Any:
        self.calls.append(kw)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        await asyncio.sleep(self.delay)
        self.running -= 1
        o = self.outcomes.pop(0) if self.outcomes else ok_response()
        if isinstance(o, Exception):
            raise o
        return o


def provider(models: FakeModels, app_secrets: object) -> GeminiProvider:
    client: Any = NS(aio=NS(models=models))
    return GeminiProvider(AppSettings(), client_factory=lambda: client)


def err(code: int, retry: str | None = None) -> errors.APIError:
    det = (
        [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry}]
        if retry
        else []
    )
    return errors.APIError(code, {"error": {"code": code, "message": "x", "details": det}})


async def gen(p: GeminiProvider, deadline: float | None = None, messages: Any = None,
              model: str = M1) -> LLMResponse:  # fmt: skip
    return await p.generate(
        model=model, system="s", messages=messages or [Message("user", "oi")], tools=[SPEC],
        deadline=monotonic() + 30 if deadline is None else deadline,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fast(_s: float) -> None:
        return None

    monkeypatch.setattr(gm, "_sleep", fast)


async def test_parameters_json_schema_passed(app_secrets: object) -> None:
    m = FakeModels()
    await gen(provider(m, app_secrets))
    decl = m.calls[0]["config"].tools[0].function_declarations[0]
    assert decl.parameters_json_schema == SPEC.parameters


async def test_sdk_retry_and_afc_disabled(app_secrets: object) -> None:
    m = FakeModels()
    await gen(provider(m, app_secrets))
    cfg = m.calls[0]["config"]
    assert cfg.automatic_function_calling.disable is True
    assert cfg.http_options.retry_options.attempts == 1


async def test_signature_roundtrip_and_drop_on_switch(app_secrets: object) -> None:
    m = FakeModels(
        [
            ok_response(
                [
                    types.Part(
                        function_call=types.FunctionCall(name="get_x", args={}),
                        thought_signature=b"sig",
                    )
                ]
            )
        ]
    )
    r = await gen(provider(m, app_secrets))
    assert r.tool_calls[0].provider_meta == b"sig"
    hist = [Message("user", "oi"),
            Message("assistant", tool_calls=r.tool_calls, model=M1),
            Message("tool", tool_call_id="call_0", tool_name="get_x", tool_result={"a": "b"},
                    model=M1)]  # fmt: skip

    p = provider(m, app_secrets)
    await gen(p, messages=hist, model=M1)
    same = m.calls[-1]["contents"][1].parts[0].thought_signature
    await gen(p, messages=hist, model=M2)
    other = m.calls[-1]["contents"][1].parts[0].thought_signature
    assert same == b"sig" and other is None


async def test_usage_and_model(app_secrets: object) -> None:
    r = await gen(provider(FakeModels(), app_secrets), model=M2)
    assert r.model == M2 and r.text == "oi" and r.finish_reason == "STOP"
    assert r.usage == Usage(10, 3, 2, 4)


async def test_timeout_is_remaining_deadline(app_secrets: object) -> None:
    m = FakeModels()
    p = provider(m, app_secrets)
    await gen(p, deadline=monotonic() + 5)
    assert 4900 <= m.calls[0]["config"].http_options.timeout <= 5000
    with pytest.raises(LLMUnavailable) as ei:
        await gen(p, deadline=monotonic() - 1)
    assert ei.value.reason == "timeout" and len(m.calls) == 1


async def test_retry_only_429_5xx_with_time(app_secrets: object) -> None:
    m = FakeModels([err(400)])
    with pytest.raises(errors.APIError):
        await gen(provider(m, app_secrets))
    assert len(m.calls) == 1
    m = FakeModels([err(503)])
    with pytest.raises(LLMUnavailable):
        await gen(provider(m, app_secrets), deadline=monotonic() + 0.2)
    assert len(m.calls) == 1
    m = FakeModels([err(503)])
    assert (await gen(provider(m, app_secrets), deadline=monotonic() + 5)).text == "oi"
    assert len(m.calls) == 2


async def test_retry_info_honored(app_secrets: object) -> None:
    m = FakeModels([err(429, "2s")])
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(m, app_secrets), deadline=monotonic() + 1)
    assert ei.value.reason == "quota" and len(m.calls) == 1


async def test_retry_cap_floor(app_secrets: object) -> None:
    m = FakeModels([err(503) for _ in range(300)])
    p = provider(m, app_secrets)
    for _ in range(100):
        with pytest.raises(LLMUnavailable):
            await gen(p)
    assert 3 <= len(m.calls) - 100 <= 10


async def test_provider_has_no_per_call_queue(app_secrets: object) -> None:
    m = FakeModels(delay=0.05)
    p = provider(m, app_secrets)
    await asyncio.gather(*(gen(p) for _ in range(10)))
    assert m.max_running == 10


def test_vertex_backend_selected(app_secrets: object, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(gm.genai, "Client", lambda **kw: seen.update(kw) or NS())
    GeminiProvider(AppSettings(llm_backend="vertex", gcp_project="p", gcp_location="global"))
    assert seen["vertexai"] is True and seen["project"] == "p" and seen["location"] == "global"
    seen.clear()
    GeminiProvider(AppSettings(gemini_api_key="k"))
    assert "vertexai" not in seen and seen["api_key"] == "k"


async def test_scripted_llm_records_calls_and_make_llm(app_secrets: object) -> None:
    r = LLMResponse("a", (), Usage(), "m")
    llm = ScriptedLLM([r, lambda msgs: LLMResponse(str(len(msgs)), (), Usage(), "m")])
    out = [await llm.generate(model="m", system="s", messages=[Message("user", "x")], tools=[],
                              deadline=1.0) for _ in range(2)]  # fmt: skip
    assert out[0] is r and out[1].text == "1"
    assert llm.calls[0].messages[0].text == "x" and llm.calls[0].system == "s"
    assert isinstance(make_llm(AppSettings(llm_provider="fake")), ScriptedLLM)
    with pytest.raises(NotImplementedError):
        make_llm(AppSettings(llm_provider="auto"))


async def test_multi_tool_call_turn_rebuilt_in_single_contents(app_secrets: object) -> None:
    m = FakeModels()
    calls = (ToolCall("c0", "a", {"x": 1}, b"s0"), ToolCall("c1", "b", {}, None))
    hist = [
        Message("user", "oi"),
        Message("assistant", text="vou ver", tool_calls=calls, provider_meta=b"st", model=M1),
        Message("tool", tool_call_id="c0", tool_name="a", tool_result={"r": 1}, model=M1),
        Message("tool", tool_call_id="c1", tool_name="b", tool_result={"r": 2}, model=M1),
    ]
    await gen(provider(m, app_secrets), messages=hist)
    cs = m.calls[0]["contents"]
    assert [c.role for c in cs] == ["user", "model", "user"]
    mp, up = cs[1].parts, cs[2].parts
    assert [(p.text, p.thought_signature) for p in mp[:1]] == [("vou ver", b"st")]
    assert [(p.function_call.name, p.thought_signature) for p in mp[1:]] == [
        ("a", b"s0"),
        ("b", None),
    ]
    assert [(p.function_response.name, p.function_response.response) for p in up] == [
        ("a", {"r": 1}),
        ("b", {"r": 2}),
    ]


@pytest.mark.parametrize(
    "details", [[{"x": 1}], "texto", None, {"error": "x"}, {"error": {"details": "x"}}]
)
async def test_retry_info_odd_shapes_do_not_raise(app_secrets: object, details: Any) -> None:
    e = errors.APIError(429, details)
    m = FakeModels([e])
    with pytest.raises(LLMUnavailable):  # sem RetryInfo: backoff padrão; sem sobra: quota
        await gen(provider(m, app_secrets), deadline=monotonic() + 0.2)


async def test_httpx_timeout_is_llm_timeout(app_secrets: object) -> None:
    m = FakeModels([httpx.ReadTimeout("t")])
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(m, app_secrets))
    assert ei.value.reason == "timeout"


async def test_empty_candidates_is_blocked(app_secrets: object) -> None:
    resp = NS(candidates=[], usage_metadata=None,
              prompt_feedback=NS(block_reason=types.BlockedReason.SAFETY))  # fmt: skip
    r = await gen(provider(FakeModels([resp]), app_secrets))
    assert r.finish_reason == "BLOCKED" and r.text is None and r.tool_calls == ()
