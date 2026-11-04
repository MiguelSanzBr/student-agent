import json
from types import SimpleNamespace as NS
from typing import Any

import httpx2
import openai
import pytest

import app.agent.gpt as gp
from app.agent.gpt import OpenAIProvider
from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall
from app.agent.messages import card_messages
from app.agent.retry import BACKOFF_S
from app.config import AppSettings
from app.data.card import StudentCard
from app.tools.base import ToolSpec
from core.clock import monotonic

SPEC = ToolSpec("get_x", "d", {"type": "object", "properties": {}, "additionalProperties": False})
M1, M2 = "gpt-6.1-terra", "gpt-6-astra"
REQ = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def item(d: dict[str, Any]) -> Any:
    return NS(
        type=d["type"], model_dump=lambda **_: d, **{k: v for k, v in d.items() if k != "type"}
    )


def msg_item(*content: dict[str, Any]) -> Any:
    parts = [NS(**c) for c in content]
    return NS(type="message", content=parts, model_dump=lambda **_: {})


def ok(output: list[Any] | None = None, **kw: Any) -> Any:
    out = output if output is not None else [msg_item({"type": "output_text", "text": "oi"})]
    return NS(
        output=out, status=kw.get("status", "completed"),
        incomplete_details=kw.get("incomplete"),
        usage=NS(input_tokens=10, output_tokens=5,
                 input_tokens_details=NS(cached_tokens=4),
                 output_tokens_details=NS(reasoning_tokens=2)),
    )  # fmt: skip


class FakeResponses:
    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.outcomes = outcomes or []
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        o = self.outcomes.pop(0) if self.outcomes else ok()
        if isinstance(o, Exception):
            raise o
        return o


def provider(r: FakeResponses, app_secrets: object) -> OpenAIProvider:
    return OpenAIProvider(AppSettings(), client_factory=lambda: NS(responses=r))  # type: ignore[arg-type,return-value]


def status_err(code: int, **headers: str) -> openai.APIStatusError:
    resp = httpx2.Response(code, headers=headers, request=REQ)
    cls = openai.RateLimitError if code == 429 else openai.APIStatusError
    return cls("x", response=resp, body=None)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    got: list[float] = []

    async def fake(s: float) -> None:
        got.append(s)

    monkeypatch.setattr(gp, "_sleep", fake)
    return got


async def gen(p: OpenAIProvider, msgs: list[Message] | None = None, model: str = M1,
              tools: list[ToolSpec] | None = None, ttl: float = 30) -> LLMResponse:  # fmt: skip
    return await p.generate(model=model, system="sys", messages=msgs or [Message("user", "oi")],
                            tools=tools if tools is not None else [SPEC],
                            deadline=monotonic() + ttl)  # fmt: skip


async def test_request_shape(app_secrets: object) -> None:
    r = FakeResponses()
    resp = await gen(provider(r, app_secrets))
    kw = r.calls[0]
    assert kw["model"] == M1 and kw["instructions"] == "sys" and kw["store"] is False
    assert kw["max_output_tokens"] == 4096 and kw["reasoning"] == {"effort": "none"}
    assert "include" not in kw and "temperature" not in kw and "top_p" not in kw
    assert kw["tools"] == [{"type": "function", "name": "get_x", "description": "d",
                            "parameters": dict(SPEC.parameters), "strict": False}]  # fmt: skip
    assert 0 < kw["timeout"] <= 30
    assert kw["input"] == [{"role": "user", "content": "oi"}]
    assert resp.text == "oi" and resp.finish_reason == "STOP" and resp.model == M1
    u = resp.usage
    assert (u.input_tokens, u.cached_tokens, u.thinking_tokens, u.output_tokens) == (10, 4, 2, 3)


async def test_effort_and_include_by_model(app_secrets: object) -> None:
    r = FakeResponses()
    await gen(provider(r, app_secrets), model="gpt-6-luna", tools=[])
    assert r.calls[0]["reasoning"] == {"effort": "none"} and "tools" not in r.calls[0]
    await gen(provider(r, app_secrets), model=M2)
    assert r.calls[1]["reasoning"] == {"effort": "low"}
    assert r.calls[1]["include"] == ["reasoning.encrypted_content"]


async def test_messages_mapping(app_secrets: object) -> None:
    card = StudentCard(text="aluno X", facts={})
    c1, c2 = ToolCall("a", "get_x", {"k": "ç"}), ToolCall("b", "get_x", {})
    msgs = [
        *card_messages(card),
        Message("user", "oi"),
        Message("assistant", "texto", (c1, c2), model=M1),
        Message("tool", tool_call_id="a", tool_name="get_x", tool_result={"v": "é"}),
        Message("tool", tool_call_id="b", tool_name="get_x", tool_result={"v": 2}),
        Message("system_note", "aviso"),
        Message("assistant"),
    ]
    r = FakeResponses()
    await gen(provider(r, app_secrets), msgs)
    inp = r.calls[0]["input"]
    assert inp[0] == {"type": "function_call", "call_id": "student-card",
                      "name": "get_student_card", "arguments": "{}"}  # fmt: skip
    assert inp[1] == {"type": "function_call_output", "call_id": "student-card",
                      "output": json.dumps({"cartao": "aluno X"}, ensure_ascii=False)}  # fmt: skip
    assert inp[2] == {"role": "user", "content": "oi"}
    assert inp[3] == {"role": "assistant", "content": "texto"}
    assert inp[4] == {"type": "function_call", "call_id": "a", "name": "get_x",
                      "arguments": '{"k": "ç"}'}  # fmt: skip
    assert inp[5]["call_id"] == "b"
    assert inp[6] == {"type": "function_call_output", "call_id": "a", "output": '{"v": "é"}'}
    assert inp[7]["call_id"] == "b" and inp[8] == {
        "role": "user",
        "content": "[nota do sistema] aviso",
    }
    assert len(inp) == 9


async def test_reasoning_resent_first_and_only_same_model(app_secrets: object) -> None:
    items = [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "zz"}]
    meta = json.dumps(items, separators=(",", ":")).encode()
    msgs = [Message("assistant", "t", model=M2, provider_meta=meta)]
    r = FakeResponses()
    await gen(provider(r, app_secrets), msgs, model=M2)
    assert r.calls[0]["input"] == [*items, {"role": "assistant", "content": "t"}]
    await gen(provider(r, app_secrets), msgs, model=M1)
    assert r.calls[1]["input"] == [{"role": "assistant", "content": "t"}]


async def test_parse_calls_reasoning_and_bad_json(app_secrets: object) -> None:
    rs = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "zz"}
    out = [
        item(rs),
        msg_item({"type": "output_text", "text": "a"}, {"type": "output_text", "text": "b"}),
        item({"type": "function_call", "call_id": "c1", "name": "get_x", "arguments": '{"q":1}'}),
        item({"type": "function_call", "call_id": "c2", "name": "get_x", "arguments": "{nope"}),
        item({"type": "function_call", "call_id": "c3", "name": "get_x", "arguments": ""}),
    ]
    resp = await gen(provider(FakeResponses([ok(out)]), app_secrets))
    assert resp.text == "ab"
    assert [(c.id, dict(c.args), c.provider_meta) for c in resp.tool_calls] == [
        ("c1", {"q": 1}, None), ("c2", {}, None), ("c3", {}, None)]  # fmt: skip
    assert resp.provider_meta == json.dumps([rs], separators=(",", ":")).encode()
    assert (await gen(provider(FakeResponses([ok()]), app_secrets))).provider_meta is None


async def test_finish_reasons(app_secrets: object) -> None:
    r = await gen(provider(FakeResponses([ok(status="incomplete",
                  incomplete=NS(reason="max_output_tokens"))]), app_secrets))  # fmt: skip
    assert r.finish_reason == "MAX_TOKENS"
    for blocked in (
        ok(status="incomplete", incomplete=NS(reason="content_filter")),
        ok([msg_item({"type": "refusal", "refusal": "não"})]),
    ):
        r = await gen(provider(FakeResponses([blocked]), app_secrets))
        assert (r.finish_reason, r.text, r.tool_calls) == ("BLOCKED", None, ())


async def test_429_retry_after_headers(app_secrets: object, sleeps: list[float]) -> None:
    r = FakeResponses([status_err(429, **{"retry-after-ms": "250"}), ok()])
    assert (await gen(provider(r, app_secrets))).text == "oi"
    r = FakeResponses([status_err(429, **{"retry-after": "1.5"}), ok()])
    await gen(provider(r, app_secrets))
    r = FakeResponses([status_err(429), ok()])
    await gen(provider(r, app_secrets))
    assert sleeps == [0.25, 1.5, BACKOFF_S] and len(r.calls) == 2


async def test_5xx_and_connection_retry_once_then_unavailable(
    app_secrets: object, sleeps: list[float]
) -> None:
    r = FakeResponses([status_err(529), ok()])
    assert (await gen(provider(r, app_secrets))).text == "oi"
    r = FakeResponses([openai.APIConnectionError(request=REQ), ok()])
    assert (await gen(provider(r, app_secrets))).text == "oi"
    r = FakeResponses([status_err(500), status_err(500), ok()])
    with pytest.raises(LLMUnavailable) as e:
        await gen(provider(r, app_secrets))
    assert e.value.reason == "server" and len(r.calls) == 2
    r = FakeResponses([status_err(429), status_err(429)])
    with pytest.raises(LLMUnavailable) as e:
        await gen(provider(r, app_secrets))
    assert e.value.reason == "quota"


async def test_timeout_no_retry(app_secrets: object, sleeps: list[float]) -> None:
    r = FakeResponses([openai.APITimeoutError(REQ), ok()])
    with pytest.raises(LLMUnavailable) as e:
        await gen(provider(r, app_secrets))
    assert e.value.reason == "timeout" and len(r.calls) == 1 and sleeps == []


async def test_other_4xx_propagates(app_secrets: object) -> None:
    with pytest.raises(openai.APIStatusError):
        await gen(provider(FakeResponses([status_err(400)]), app_secrets))


async def test_deadline_exhausted(app_secrets: object) -> None:
    r = FakeResponses()
    with pytest.raises(LLMUnavailable) as e:
        await gen(provider(r, app_secrets), ttl=-1)
    assert e.value.reason == "timeout" and r.calls == []


async def test_no_retry_without_time_or_budget(
    app_secrets: object, sleeps: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    r = FakeResponses([status_err(500), ok()])
    with pytest.raises(LLMUnavailable):
        await gen(provider(r, app_secrets), ttl=0.4)  # sobra < espera + MIN_CALL_S
    p = provider(FakeResponses([status_err(500), ok()]), app_secrets)
    monkeypatch.setattr(p._budget, "may_retry", lambda now: False)
    with pytest.raises(LLMUnavailable):
        await gen(p)
    assert sleeps == []


def real(output: list[dict[str, Any]], status: str = "completed", **kw: Any) -> Any:
    from openai.types.responses import Response

    return Response.model_validate({
        "id": "resp_1", "object": "response", "created_at": 0, "model": M1, "status": status,
        "output": output, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "usage": {"input_tokens": 10, "output_tokens": 5,
                  "input_tokens_details": {"cached_tokens": 4, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 2}, "total_tokens": 15},
        **kw,
    })  # fmt: skip


def real_msg(part: dict[str, Any]) -> dict[str, Any]:
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
            "content": [part]}  # fmt: skip


async def test_parse_real_sdk_objects(app_secrets: object) -> None:
    rs = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "zz"}
    out = [
        rs,
        real_msg({"type": "output_text", "text": "oi", "annotations": []}),
        {"type": "function_call", "id": "fc_1", "call_id": "c1", "name": "get_x",
         "arguments": '{"q":1}', "status": "completed"},
    ]  # fmt: skip
    r = await gen(provider(FakeResponses([real(out)]), app_secrets))
    assert r.text == "oi" and r.finish_reason == "STOP"
    assert [(c.id, c.name, dict(c.args)) for c in r.tool_calls] == [("c1", "get_x", {"q": 1})]
    assert json.loads(r.provider_meta or b"")[0] == rs
    assert (r.usage.input_tokens, r.usage.cached_tokens, r.usage.thinking_tokens,
            r.usage.output_tokens) == (10, 4, 2, 3)  # fmt: skip
    inc = real([], "incomplete", incomplete_details={"reason": "max_output_tokens"})
    assert (await gen(provider(FakeResponses([inc]), app_secrets))).finish_reason == "MAX_TOKENS"
    ref = real([real_msg({"type": "refusal", "refusal": "não"})])
    r = await gen(provider(FakeResponses([ref]), app_secrets))
    assert (r.finish_reason, r.text) == ("BLOCKED", None)


async def test_odd_status_warns_and_stops(
    app_secrets: object, caplog: pytest.LogCaptureFixture
) -> None:
    r = await gen(provider(FakeResponses([real([], "failed")]), app_secrets))
    assert r.finish_reason == "STOP" and "failed" in caplog.text


async def test_insufficient_quota_no_retry(app_secrets: object, sleeps: list[float]) -> None:
    resp = httpx2.Response(429, request=REQ)
    e = openai.RateLimitError("x", response=resp, body={"code": "insufficient_quota"})
    r = FakeResponses([e, ok()])
    with pytest.raises(LLMUnavailable) as ex:
        await gen(provider(r, app_secrets))
    assert ex.value.reason == "quota" and len(r.calls) == 1 and sleeps == []


async def test_no_tools_sends_neither_tools_nor_tool_choice(app_secrets: object) -> None:
    r = FakeResponses()
    hist = [*card_messages(StudentCard(text="x", facts={})), Message("user", "oi")]
    await gen(provider(r, app_secrets), hist, tools=[])
    assert "tools" not in r.calls[0] and "tool_choice" not in r.calls[0]


async def test_empty_text_messages_skipped(app_secrets: object) -> None:
    r = FakeResponses()
    msgs = [Message("user", ""), Message("user", None), Message("system_note", ""),
            Message("assistant", ""), Message("user", "oi")]  # fmt: skip
    await gen(provider(r, app_secrets), msgs)
    assert r.calls[0]["input"] == [{"role": "user", "content": "oi"}]
