"""Confirmação e feedback: portões que rodam antes de qualquer acesso ao banco."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api.chat as chat_mod
from app.config import AppSettings
from app.infra.stores.drafts import DraftStore
from app.tools.base import DraftCard, DraftKey
from tests.e2e.test_chat import cookies, make_app
from tests.unit.turn_env import OK, OTHER, SCOPE, scripted

pytestmark = pytest.mark.usefixtures("app_secrets")
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _scope(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scope(request: Any, pk: Any) -> Any:
        return SCOPE

    monkeypatch.setattr(chat_mod, "_load_scope", scope)


@pytest.fixture
def client() -> Iterator[TestClient]:
    s = AppSettings()
    with TestClient(make_app(s, scripted(OK))) as c:
        c.cookies.update(cookies(s))
        yield c


def _card(draft_id: str = "d1") -> DraftCard:
    return DraftCard(
        draft_id, "tok", DraftKey("DOC-001", None), "Declaração", None, "R$ 0,00", "02/11/2026",
        "", "aviso", "conv-1", "1",
    )  # fmt: skip


def test_csrf_403(client: TestClient) -> None:
    body = {"draft_id": "d1"}
    evil = client.post("/protocols/confirm", json=body, headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    assert client.post("/protocols/confirm", json=body).status_code == 403  # sem Origin
    form = client.post("/protocols/confirm", data=body, headers=ORIGIN)  # urlencoded
    assert form.status_code == 403
    fb = {"trace_id": "t", "useful": False}
    assert (
        client.post("/feedback", json=fb, headers={"Origin": "http://evil.example"}).status_code
        == 403
    )


def test_confirm_requires_login(client: TestClient) -> None:
    client.cookies.clear()
    assert (
        client.post("/protocols/confirm", json={"draft_id": "d1"}, headers=ORIGIN).status_code
        == 401
    )
    assert (
        client.post("/feedback", json={"trace_id": "t", "useful": True}, headers=ORIGIN).status_code
        == 401
    )


async def test_other_students_draft_409(client: TestClient) -> None:
    redis = client.app.state.redis  # type: ignore[attr-defined]
    await DraftStore(redis, AppSettings().limits).put(OTHER, "conv-1", _card())
    r = client.post("/protocols/confirm", json={"draft_id": "d1"}, headers=ORIGIN)
    assert r.status_code == 409
    again = DraftStore(redis, AppSettings().limits)
    assert await again.take(OTHER, "d1") is not None  # o rascunho do outro segue intacto


def test_feedback_csrf_missing_origin_and_form(client: TestClient) -> None:
    fb = {"trace_id": "t", "useful": True}
    assert client.post("/feedback", json=fb).status_code == 403  # sem Origin
    assert client.post("/feedback", data=fb, headers=ORIGIN).status_code == 403  # urlencoded
