import json
from uuid import uuid4

import httpx
import pytest

from evals.client import ChatClient, Rejected


def sse(*events: tuple[str, dict[str, object]]) -> str:
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events)


async def test_ask_builds_turn_record() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = sse(
            ("meta", {"trace_id": "t1"}), ("ping", {}),
            ("draft", {"draft_id": "d1", "token": "tk"}),
            ("debug", {"tools": ["get_financial_summary"], "pre_guard_answer": "pre"}),
            ("answer", {"text": "Você deve R$ 1,00."}),
            ("done", {"status": "ok", "trace_id": "t1"}),
        )  # fmt: skip
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    c = ChatClient("http://api", transport=httpx.MockTransport(handler))
    rec = await c.ask("jwt", "conv", "oi")
    assert rec.answer == "Você deve R$ 1,00." and rec.draft and rec.trace_id == "t1"
    assert rec.tools_called == ("get_financial_summary",) and rec.pre_guard_answer == "pre"
    assert seen[0].headers["cookie"] == "session=jwt"
    assert json.loads(seen[0].content) == {"conversation_id": "conv", "text": "oi"}


async def test_ask_parses_crlf_sse() -> None:
    body = sse(("answer", {"text": "ok"}), ("done", {"status": "ok", "trace_id": "t"}))

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.replace("\n", "\r\n").encode())

    c = ChatClient("http://api", transport=httpx.MockTransport(handler))
    rec = await c.ask("jwt", "conv", "oi")
    assert rec.answer == "ok" and rec.trace_id == "t"


async def test_ask_rejected_carries_retry_after() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={}, headers={"Retry-After": "7"})

    c = ChatClient("http://api", transport=httpx.MockTransport(handler))
    with pytest.raises(Rejected) as e:
        await c.ask("jwt", "conv", "oi")
    assert e.value.status == 503 and e.value.retry_after == 7.0


async def test_confirm_feedback_a2a_use_expected_routes() -> None:
    paths: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        paths.append((req.url.path, req.headers.get("authorization", "")))
        return httpx.Response(200, json={"ok": True})

    c = ChatClient("http://api", transport=httpx.MockTransport(handler))
    await c.confirm("jwt", "d1")
    await c.feedback("jwt", "t1", False)
    await c.a2a_send("jwt", "ctx", "oi")
    assert [p for p, _ in paths] == ["/protocols/confirm", "/feedback", "/a2a"]
    assert paths[2][1] == "Bearer jwt"


def test_mint_uses_issue_token(app_secrets: object) -> None:
    from app.api.auth import verify_token
    from app.config import AppSettings
    from evals.tokens import mint

    pk = uuid4()
    tok = mint(pk, "chat")
    assert verify_token(tok, "chat", AppSettings()) == pk  # type: ignore[call-arg]


async def test_ask_reads_debug_tool_calls_values_and_served_model() -> None:
    body = sse(
        ("meta", {"trace_id": "t1", "span_id": "s1"}),
        ("debug", {"tool_calls": [{"name": "get_x"}], "tool_values": {"get_x": ["v"]}}),
        ("answer", {"text": "ok"}), ("done", {"status": "ok", "model": "m1", "trace_id": "t1"}),
    )  # fmt: skip
    c = ChatClient("http://api", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=body.encode())))  # fmt: skip
    rec = await c.ask("jwt", "conv", "oi")
    assert rec.tools_called == ("get_x",)
    assert c.last_meta == {"trace_id": "t1", "span_id": "s1", "tool_values": {"get_x": ["v"]},
                           "model": "m1"}  # fmt: skip
