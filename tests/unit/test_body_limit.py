from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.infra.bodylimit import MAX_BODY_BYTES, BodyLimit
from app.main import app as main_app


def _app() -> FastAPI:
    a = FastAPI()
    a.add_middleware(BodyLimit)

    @a.post("/x")
    async def x() -> dict[str, bool]:
        return {"ok": True}

    return a


def test_over_limit_is_413_and_at_limit_passes() -> None:
    c = TestClient(_app())
    assert c.post("/x", content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413
    assert c.post("/x", content=b"x" * MAX_BODY_BYTES).status_code == 200


def test_main_app_has_the_limit() -> None:
    assert any(m.cls is BodyLimit for m in main_app.user_middleware)
