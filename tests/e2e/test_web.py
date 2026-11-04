import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import web

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
