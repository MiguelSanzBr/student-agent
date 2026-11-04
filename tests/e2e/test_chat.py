import asyncio
import json
from collections.abc import Iterator, MutableMapping
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.chat as chat_mod
from app.api import ROUTERS
from app.api.auth import COOKIE, issue_token
from app.config import AppSettings
from tests.unit.redis_fake import FakeRedis
from tests.unit.turn_env import (
    OK,
    SCOPE,
    SUB,
    HangLLM,
    make_service,
    patch_db,
    scripted,
    slots_in_use,
)

pytestmark = pytest.mark.usefixtures("app_secrets")


@pytest.fixture(autouse=True)
def _db(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_db(monkeypatch)

    async def scope(request: Any, pk: Any) -> Any:
        return SCOPE

    monkeypatch.setattr(chat_mod, "_load_scope", scope)

    async def first_name(request: Any, scope: Any) -> str:
        return "Maria"

    monkeypatch.setattr(chat_mod, "_first_name", first_name)


def make_app(settings: AppSettings, llm: Any) -> FastAPI:
    app = FastAPI()
    for r in ROUTERS:
        app.include_router(r)
    app.state.settings = settings
    app.state.redis = FakeRedis()
    app.state.turns = make_service(settings, llm, app.state.redis)
    app.state.pool = None
    return app


def cookies(settings: AppSettings) -> dict[str, str]:
    return {COOKIE: issue_token(SUB, "chat", settings)}


def events(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("\n\n"):
        lines = dict(ln.split(": ", 1) for ln in block.splitlines() if ": " in ln)
        if lines.get("event") and lines["event"] != "ping":
            out.append({"event": lines["event"], **json.loads(lines["data"])})
    return out


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings()


@pytest.fixture
def client(settings: AppSettings) -> Iterator[TestClient]:
    with TestClient(make_app(settings, scripted(OK, OK, OK))) as c:
        c.cookies.update(cookies(settings))
        yield c


def post(c: TestClient, text: str = "oi") -> Any:
    return c.post("/chat", json={"conversation_id": "c1", "text": text})


def test_progress_then_answer(client: TestClient) -> None:
    r = post(client)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events(r.text)
    assert [e["event"] for e in ev][0] == "meta" and ev[-1]["event"] == "done"
    assert next(e for e in ev if e["event"] == "answer")["text"] == OK


def test_unauthenticated_401(client: TestClient) -> None:
    client.cookies.clear()
    assert post(client).status_code == 401


def test_extra_body_422(client: TestClient) -> None:
    r = client.post("/chat", json={"conversation_id": "c", "text": "oi", "sub": "x"})
    assert r.status_code == 422


def test_long_input_error(client: TestClient) -> None:
    ev = events(post(client, "a" * 2500).text)
    assert any(e["event"] == "error" and "2000" in e["message"] for e in ev)


def test_rate_limit_429_retry_after(settings: AppSettings) -> None:
    settings.slo.limits["default"].msgs_per_min = 1
    with TestClient(make_app(settings, scripted(OK, OK))) as c:
        c.cookies.update(cookies(settings))
        assert post(c).status_code == 200
        r = post(c)
        assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
        assert "event:" not in r.text


def test_admit_before_stream_http_503_retry_after(settings: AppSettings) -> None:
    settings.slo.quotas[settings.llm_model].rpm = 1
    with TestClient(make_app(settings, scripted(OK))) as c:
        c.cookies.update(cookies(settings))
        r = post(c)
        assert r.status_code == 503 and int(r.headers["retry-after"]) >= 1
        assert "event:" not in r.text and r.headers["content-type"].startswith("application/json")


def test_global_spend_503(settings: AppSettings) -> None:
    app = make_app(settings, scripted(OK))
    with TestClient(app) as c:
        c.cookies.update(cookies(settings))
        asyncio.run(app.state.turns.spend.add(Decimal("20")))
        r = post(c)
        assert r.status_code == 503 and "retry-after" in r.headers


def test_me_returns_own_first_name(client: TestClient) -> None:
    r = client.get("/me")
    assert r.status_code == 200
    assert r.json() == {
        "first_name": "Maria",
        "enrollments": [SCOPE.enrollments[0].label],
        "demo_mode": False,
        "suggested": [],
    }


async def call_asgi(
    app: FastAPI, settings: AppSettings, *, disconnect_on: str | None, fail_send: bool = False
) -> list[bytes]:
    """Chama o app via ASGI cru para simular o cliente que cai (before/after o 1º byte)."""
    body = json.dumps({"conversation_id": "c1", "text": "oi"}).encode()
    cookie = f"{COOKIE}={cookies(settings)[COOKIE]}".encode()
    scope: MutableMapping[str, Any] = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "path": "/chat", "raw_path": b"/chat", "query_string": b"", "scheme": "http",
        "server": ("t", 80), "client": ("c", 1), "root_path": "",
        "headers": [(b"host", b"t"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()), (b"cookie", cookie)],
        "app": app, "state": {},
    }  # fmt: skip
    gone, sent, got_body = asyncio.Event(), [], False

    async def receive() -> dict[str, Any]:
        nonlocal got_body
        if not got_body:
            got_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        if disconnect_on is None:
            await asyncio.sleep(60)
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(m: MutableMapping[str, Any]) -> None:
        if fail_send:
            raise OSError("cliente caiu")
        sent.append(m.get("body", b""))
        if disconnect_on and disconnect_on.encode() in m.get("body", b""):
            gone.set()

    if disconnect_on is None and fail_send is False:
        gone.set()
    try:
        await asyncio.wait_for(app(scope, receive, send), 10)
    except OSError:
        pass
    return sent


async def test_disconnect_cancels_and_settles(settings: AppSettings) -> None:
    app = make_app(settings, HangLLM())
    async with app.router.lifespan_context(app):
        await call_asgi(app, settings, disconnect_on="event: meta")
        assert await slots_in_use(app.state.turns) == 0


async def test_disconnect_before_first_byte_settles(settings: AppSettings) -> None:
    app = make_app(settings, scripted(OK))
    async with app.router.lifespan_context(app):
        await call_asgi(app, settings, disconnect_on="x", fail_send=True)
        assert await slots_in_use(app.state.turns) == 0
