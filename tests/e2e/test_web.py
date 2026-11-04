import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.chat as chat_mod
from app.api import web
from app.config import AppSettings
from app.tools.base import DraftCard, DraftKey, default_ontology
from tests.e2e.test_chat import OK, cookies, events, make_app, post, scripted
from tests.unit.turn_env import SCOPE, patch_db

WEB = Path(__file__).resolve().parents[2] / "app" / "web"
CSP = "default-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(web.router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "ctype"),
    [("/", "text/html"), ("/static/app.js", "javascript"), ("/static/app.css", "text/css")],
)
def test_security_headers(client: TestClient, path: str, ctype: str) -> None:
    r = client.get(path)
    assert r.status_code == 200 and ctype in r.headers["content-type"]
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"


def test_static_unknown_404(client: TestClient) -> None:
    assert client.get("/static/../web.py").status_code in (404, 400)
    assert client.get("/static/nada.js").status_code == 404
    for bad in ("..%2fweb.py", "index.html%00", "%2e%2e%2fweb.py"):
        assert client.get(f"/static/{bad}").status_code == 404


def test_js_guards_concurrency_and_localizes_errors() -> None:
    js = (WEB / "app.js").read_text()
    assert "if (busy) return" in js and "AbortController" in js and "maxLength = 500" in js
    assert re.search(r"catch \{ throw new ApiError\(ERR", js)


def test_index_is_pt_br_accessible_and_has_no_inline_script(client: TestClient) -> None:
    html = client.get("/").text
    assert '<html lang="pt-BR">' in html and 'name="viewport"' in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html) and "style=" not in html
    assert 'aria-live="polite"' in html


def test_js_no_innerhtml() -> None:
    js = (WEB / "app.js").read_text()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in js


def test_new_conversation_button(client: TestClient) -> None:
    assert "Nova conversa" in client.get("/").text
    assert "new-chat" in (WEB / "app.js").read_text()


def test_demo_banner_when_replay(client: TestClient) -> None:
    html, js = client.get("/").text, (WEB / "app.js").read_text()
    assert 'id="demo-banner"' in html and "hidden" in html
    assert "modo demonstração" in html and "me.demo_mode" in js and "me.suggested" in js


def test_gen090_label() -> None:
    js = (WEB / "app.js").read_text()
    assert re.search(r'"GEN-090":\s*"Falar com a secretaria"', js)
    assert "service_code" in js  # o rótulo vem do código; "GEN-090" nunca é texto exibido
    assert not re.search(r"textContent\s*=\s*[^;]*['\"]GEN-090", js)


def test_js_uses_server_fields_and_endpoints() -> None:
    js = (WEB / "app.js").read_text()
    for needle in (
        "/chat",
        "/me",
        "/protocols/confirm",
        "/feedback",
        "draft_id",
        "description",
        "warning",
        "fee",
        "due_date",
        "trace_id",
        "mensagem",
    ):
        assert needle in js


@pytest.fixture
def _e2e(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_db(monkeypatch)

    async def scope(request: Any, pk: Any) -> Any:
        return SCOPE

    async def first_name(request: Any, scope: Any) -> str:
        return "Maria"

    monkeypatch.setattr(chat_mod, "_load_scope", scope)
    monkeypatch.setattr(chat_mod, "_first_name", first_name)


@pytest.mark.usefixtures("app_secrets", "_e2e")
def test_gen090_draft_payload_label() -> None:
    """D8: o rótulo vem do servidor (`service_name_pt`) e o JS só troca pelo código."""
    settings = AppSettings()
    onto_name = default_ontology().service("GEN-090").ui_label
    card = DraftCard(
        "d1", "tok", DraftKey("GEN-090", None), onto_name, None, "R$ 0,00", "05/11/2026",
        "Quero falar com a secretaria", "aviso", "c1", "1",
    )  # fmt: skip
    app = make_app(settings, scripted(OK))

    async def handle(**kw: Any) -> None:
        await kw["emit"]({"event": "draft", **card.public()})
        await kw["emit"]({"event": "done", "status": "ok"})

    app.state.turns.handle = handle
    with TestClient(app) as c:
        c.cookies.update(cookies(settings))
        ev = events(post(c).text)
    d = next(e for e in ev if e["event"] == "draft")
    assert d["service_code"] == "GEN-090" and d["service_name_pt"] == "Falar com a secretaria"
    assert "conversation_id" not in d and "token" not in d
