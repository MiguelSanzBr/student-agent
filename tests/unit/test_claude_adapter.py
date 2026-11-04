import json
import logging
from collections.abc import Callable
from types import SimpleNamespace as NS
from typing import Any

import anthropic
import httpx2
import pytest

import app.agent.claude as cl
from app.agent.claude import MAX_OUTPUT_TOKENS, START_TEXT, AnthropicProvider
from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall
from app.agent.messages import card_messages
from app.agent.retry import BACKOFF_S
from app.config import AppSettings
from app.tools.base import ToolSpec
from core.clock import monotonic

SPEC = ToolSpec("get_x", "d", {"type": "object", "properties": {}, "additionalProperties": False})
M1, M2 = "claude-sonnet-5-5", "claude-opus-5-5"
REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
THINK = {"type": "thinking", "thinking": "hm", "signature": "sig"}
REDACTED = {"type": "redacted_thinking", "data": "xyz"}


def usage(**kw: Any) -> NS:
    return NS(**{"input_tokens": 10, "output_tokens": 3, "cache_read_input_tokens": 4,
                 "cache_creation_input_tokens": 2, **kw})  # fmt: skip


def block(d: dict[str, Any]) -> NS:
    return NS(**d, model_dump=lambda **_kw: dict(d))


def ok(content: list[Any] | None = None, stop: str = "end_turn", **kw: Any) -> Any:
    return NS(content=content if content is not None else [block({"type": "text", "text": "oi"})],
              stop_reason=stop, usage=usage(), stop_details=None, **kw)  # fmt: skip


class FakeMessages:
    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = outcomes or []
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        o = self.outcomes.pop(0) if self.outcomes else ok()
        if isinstance(o, Exception):
            raise o
        return o


@pytest.fixture
def provider(app_secrets: object) -> Callable[[FakeMessages], AnthropicProvider]:
    def make(fm: FakeMessages) -> AnthropicProvider:
        client: Any = NS(beta=NS(messages=fm))
        return AnthropicProvider(AppSettings(), client_factory=lambda: client)

    return make


def status(code: int, headers: dict[str, str] | None = None) -> anthropic.APIStatusError:
    resp = httpx2.Response(code, headers=headers or {}, request=REQ)
    cls = anthropic.RateLimitError if code == 429 else anthropic.APIStatusError
    return cls("x", response=resp, body=None)


async def gen(p: AnthropicProvider, deadline: float | None = None, messages: Any = None,
              model: str = M1) -> LLMResponse:  # fmt: skip
    return await p.generate(
        model=model, system="s", messages=messages or [Message("user", "oi")], tools=[SPEC],
        deadline=monotonic() + 30 if deadline is None else deadline,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fast(s: float) -> None:
        slept.append(s)

    monkeypatch.setattr(cl, "_sleep", fast)
    return slept


async def test_request_shape(provider: Callable[[FakeMessages], AnthropicProvider]) -> None:
    fm = FakeMessages()
    r = await gen(provider(fm), deadline=monotonic() + 20)
    kw = fm.calls[0]
    assert kw["model"] == M1 and kw["system"] == "s" and kw["max_tokens"] == MAX_OUTPUT_TOKENS
    assert kw["tools"] == [{"name": "get_x", "description": "d", "input_schema": SPEC.parameters}]
    assert kw["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert kw["betas"] == ["thinking-binding-controls-2026-08-01"]
    assert kw["output_config"] == {"effort": "low"}
    assert kw["cache_control"] == {"type": "ephemeral"}
    assert not {"temperature", "top_p", "top_k"} & kw.keys()
    assert 0 < kw["timeout"] <= 20
    assert r.text == "oi" and r.model == M1 and r.finish_reason == "STOP"
    assert r.provider_meta is None


async def test_no_tools_omits_key(provider: Callable[[FakeMessages], AnthropicProvider]) -> None:
    fm = FakeMessages()
    await provider(fm).generate(model=M1, system="s", messages=[Message("user", "a")],
                                tools=[], deadline=monotonic() + 5)  # fmt: skip
    assert "tools" not in fm.calls[0]


async def test_message_mapping(provider: Callable[[FakeMessages], AnthropicProvider]) -> None:
    msgs = [
        *card_messages(NS(text="C")),  # type: ignore[arg-type]
        Message("user", "oi"),
        Message("assistant", "t", (ToolCall("a", "f", {"x": 1}), ToolCall("b", "g", {}))),
        Message("tool", tool_call_id="a", tool_name="f", tool_result={"k": "ç"}),
        Message("tool", tool_call_id="b", tool_name="g", tool_result={}),
        Message("system_note", "n"),
        Message("assistant"),  # vazio: pula
        Message("user", "de novo"),
    ]
    fm = FakeMessages()
    await gen(provider(fm), messages=msgs)
    out = fm.calls[0]["messages"]
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant", "user"]
    assert out[0]["content"] == [{"type": "text", "text": START_TEXT}]
    assert out[1]["content"] == [
        {"type": "tool_use", "id": "student-card", "name": "get_student_card", "input": {}}
    ]
    assert out[2]["content"][0] == {"type": "tool_result", "tool_use_id": "student-card",
                                    "content": json.dumps({"cartao": "C"})}  # fmt: skip
    assert out[2]["content"][1] == {"type": "text", "text": "oi"}
    assert out[3]["content"] == [
        {"type": "text", "text": "t"},
        {"type": "tool_use", "id": "a", "name": "f", "input": {"x": 1}},
        {"type": "tool_use", "id": "b", "name": "g", "input": {}},
    ]
    assert out[4]["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": '{"k": "ç"}'},
        {"type": "tool_result", "tool_use_id": "b", "content": "{}"},
        {"type": "text", "text": "[nota do sistema] n"},
        {"type": "text", "text": "de novo"},
    ]


async def test_user_first_needs_no_start_text(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    fm = FakeMessages()
    await gen(provider(fm))
    assert fm.calls[0]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "oi"}]}
    ]


async def test_thinking_resent_first_only_same_model_text_from_m_text(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    meta = json.dumps([THINK, REDACTED], separators=(",", ":"))
    msgs = [
        Message("user", "a"),
        Message("assistant", "guardado", provider_meta=meta.encode(), model=M1),
        Message("user", "b"),
    ]
    fm = FakeMessages()
    await gen(provider(fm), messages=msgs, model=M1)
    c = fm.calls[0]["messages"][1]["content"]
    assert c[:2] == [THINK, REDACTED] and c[2] == {"type": "text", "text": "guardado"}
    fm = FakeMessages()
    await gen(provider(fm), messages=msgs, model=M2)
    assert fm.calls[0]["messages"][1]["content"] == [{"type": "text", "text": "guardado"}]


async def test_parse_text_calls_thinking_usage(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    content = [
        block(THINK),
        block({"type": "text", "text": "a"}),
        block({"type": "tool_use", "id": "t1", "name": "f", "input": {"q": 1}}),
        block(REDACTED),
        block({"type": "text", "text": "b"}),
    ]
    fm = FakeMessages([ok(content)])
    r = await gen(provider(fm))
    assert r.text == "ab" and r.tool_calls == (ToolCall("t1", "f", {"q": 1}),)
    assert r.provider_meta == json.dumps([THINK, REDACTED], separators=(",", ":")).encode()
    assert (r.usage.input_tokens, r.usage.cached_tokens) == (16, 4)
    assert (r.usage.output_tokens, r.usage.thinking_tokens) == (3, 0)


async def test_thinking_with_empty_text_still_kept(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    empty = {"type": "thinking", "thinking": "", "signature": "s2"}
    r = await gen(provider(FakeMessages([ok([block(empty)])])))
    assert r.text is None and r.provider_meta == json.dumps([empty], separators=(",", ":")).encode()


async def test_max_tokens_and_refusal(
    caplog: pytest.LogCaptureFixture, provider: Callable[[FakeMessages], AnthropicProvider]
) -> None:
    r = await gen(provider(FakeMessages([ok(stop="max_tokens")])))
    assert r.finish_reason == "MAX_TOKENS" and r.text == "oi"
    refusal = ok([block({"type": "text", "text": "x"})], stop="refusal")
    refusal.stop_details = NS(category="cyber")
    with caplog.at_level(logging.WARNING):
        r = await gen(provider(FakeMessages([refusal])))
    assert (r.finish_reason, r.text, r.tool_calls) == ("BLOCKED", None, ())
    assert "cyber" in caplog.text


async def test_429_retry_after_then_ok(
    sleeps: list[float], provider: Callable[[FakeMessages], AnthropicProvider]
) -> None:
    fm = FakeMessages([status(429, {"retry-after": "1.5"})])
    r = await gen(provider(fm))
    assert r.text == "oi" and sleeps == [1.5] and len(fm.calls) == 2


async def test_429_without_header_uses_backoff(
    sleeps: list[float], provider: Callable[[FakeMessages], AnthropicProvider]
) -> None:
    await gen(provider(FakeMessages([status(429)])))
    assert sleeps == [BACKOFF_S]


@pytest.mark.parametrize("exc", [status(500), status(529),
                                 anthropic.APIConnectionError(request=REQ)])  # fmt: skip
async def test_server_errors_retry_once(
    exc: Exception, sleeps: list[float], provider: Callable[[FakeMessages], AnthropicProvider]
) -> None:
    fm = FakeMessages([exc])
    assert (await gen(provider(fm))).text == "oi"
    assert sleeps == [BACKOFF_S]
    fm = FakeMessages([exc, exc, exc])
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(fm))
    assert ei.value.reason == "server" and len(fm.calls) == 2


async def test_second_429_is_quota(provider: Callable[[FakeMessages], AnthropicProvider]) -> None:
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(FakeMessages([status(429), status(429)])))
    assert ei.value.reason == "quota"


async def test_timeout_no_retry(provider: Callable[[FakeMessages], AnthropicProvider]) -> None:
    fm = FakeMessages([anthropic.APITimeoutError(REQ)])
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(fm))
    assert ei.value.reason == "timeout" and len(fm.calls) == 1


async def test_other_4xx_propagates(provider: Callable[[FakeMessages], AnthropicProvider]) -> None:
    with pytest.raises(anthropic.APIStatusError):
        await gen(provider(FakeMessages([status(400)])))


async def test_deadline_passed_does_not_call(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    fm = FakeMessages()
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(fm), deadline=monotonic() - 1)
    assert ei.value.reason == "timeout" and not fm.calls


async def test_no_time_for_retry_raises_without_second_call(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    fm = FakeMessages([status(429, {"retry-after": "30"})])
    with pytest.raises(LLMUnavailable) as ei:
        await gen(provider(fm), deadline=monotonic() + 5)
    assert ei.value.reason == "quota" and len(fm.calls) == 1


async def test_budget_exhausted_no_retry(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    fm = FakeMessages([status(500)] * 10)
    p = provider(fm)
    for _ in range(3):
        with pytest.raises(LLMUnavailable):
            await gen(p)  # cada uma gasta 1 retry do piso de 3/min
    n = len(fm.calls)
    with pytest.raises(LLMUnavailable):
        await gen(p)
    assert len(fm.calls) == n + 1  # sem nova tentativa


async def test_no_tools_with_history_declares_stubs_and_forbids_calls(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    msgs = [
        *card_messages(NS(text="C")),  # type: ignore[arg-type]
        Message("assistant", None, (ToolCall("a", "zeta", {}), ToolCall("b", "alfa", {}))),
        Message("tool", tool_call_id="a", tool_name="zeta", tool_result={}),
        Message("user", "oi"),
    ]
    fm = FakeMessages()
    await provider(fm).generate(
        model=M1, system="s", messages=msgs, tools=[], deadline=monotonic() + 5
    )
    kw = fm.calls[0]
    stub = {"description": "(só histórico)", "input_schema": {"type": "object"}}
    assert kw["tools"] == [{"name": n, **stub} for n in ("alfa", "get_student_card", "zeta")]
    assert kw["tool_choice"] == {"type": "none"}


async def test_with_tools_no_tool_choice(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    fm = FakeMessages()
    await gen(provider(fm), messages=[*card_messages(NS(text="C")), Message("user", "x")])  # type: ignore[arg-type]
    assert "tool_choice" not in fm.calls[0] and len(fm.calls[0]["tools"]) == 1


async def test_no_empty_text_blocks(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    msgs = [
        Message("user", ""),
        Message("user", "a"),
        Message("assistant", ""),
        Message("system_note", ""),
        Message("assistant", "", (ToolCall("a", "f", {}),)),
        Message("tool", tool_call_id="a", tool_name="f", tool_result={}),
    ]
    fm = FakeMessages()
    await gen(provider(fm), messages=msgs)
    out = fm.calls[0]["messages"]
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": "a"}]}
    assert out[1]["content"] == [{"type": "tool_use", "id": "a", "name": "f", "input": {}}]
    assert all(b.get("text") != "" for m in out for b in m["content"])


async def test_empty_user_then_assistant_first_still_starts_with_user(
    provider: Callable[[FakeMessages], AnthropicProvider],
) -> None:
    fm = FakeMessages()
    await gen(provider(fm), messages=[Message("user", ""), Message("assistant", "oi")])
    out = fm.calls[0]["messages"]
    assert out[0] == {"role": "user", "content": [{"type": "text", "text": START_TEXT}]}


async def test_real_client_wire_format(app_secrets: object) -> None:
    seen: list[httpx2.Request] = []
    body = {
        "id": "msg_1", "type": "message", "role": "assistant", "model": M1,
        "content": [
            {"type": "thinking", "thinking": "hm", "signature": "sig"},
            {"type": "text", "text": "ola"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_x", "input": {"q": 1}},
        ],
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 4,
                  "cache_creation_input_tokens": 2},
    }  # fmt: skip

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(200, json=body)

    client = anthropic.AsyncAnthropic(
        api_key="k",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    p = AnthropicProvider(AppSettings(), client_factory=lambda: client)
    r = await gen(p, messages=[*card_messages(NS(text="C")), Message("user", "oi")])  # type: ignore[arg-type]
    req = seen[0]
    assert req.headers["anthropic-beta"] == "thinking-binding-controls-2026-08-01"
    j = json.loads(req.content)
    assert j["thinking"] == {
        "type": "adaptive",
        "block_binding": {"prefix_mismatch_behavior": "drop_block"},
    }
    assert j["output_config"] == {"effort": "low"} and j["cache_control"] == {"type": "ephemeral"}
    assert j["max_tokens"] == MAX_OUTPUT_TOKENS and j["system"] == "s" and j["model"] == M1
    assert j["tools"] == [{"name": "get_x", "description": "d", "input_schema": SPEC.parameters}]
    assert "tool_choice" not in j
    assert [m["role"] for m in j["messages"]] == ["user", "assistant", "user"]
    assert j["messages"][1]["content"][0]["type"] == "tool_use"
    assert j["messages"][2]["content"][0]["type"] == "tool_result"
    assert r.text == "ola" and r.tool_calls == (ToolCall("toolu_1", "get_x", {"q": 1}),)
    assert json.loads(r.provider_meta or b"") == [THINK]
    assert (r.usage.input_tokens, r.usage.cached_tokens, r.usage.output_tokens) == (16, 4, 5)
