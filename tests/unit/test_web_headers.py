import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.web import router

CSP = "default-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'"


@pytest.mark.parametrize("path", ["/", "/static/index.html", "/static/app.js", "/static/app.css"])
def test_web_security_headers(path: str) -> None:
    a = FastAPI()
    a.include_router(router)
    r = TestClient(a).get(path)
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-cache"


def test_web_only_serves_the_fixed_files() -> None:
    a = FastAPI()
    a.include_router(router)
    c = TestClient(a)
    assert c.get("/static/../config.py").status_code == 404
    assert c.get("/static/outro.txt").status_code == 404
