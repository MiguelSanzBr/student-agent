import json
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api import ROUTERS
from app.api.auth import get_person_a2a, get_person_cookie, issue_token, verify_token
from app.config import AppSettings
from app.infra.stores.tickets import LoginTickets
from app.main import lifespan, startup_checks
from core.clock import wall_now
from tests.unit.redis_fake import FakeRedis

GOLDEN = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")


class PingRedis(FakeRedis):
    def __init__(self, ok: bool = True) -> None:
        super().__init__()
        self.ok = ok

    async def ping(self) -> bool:
        if not self.ok:
            raise ConnectionError("redis fora")
        return True


class _Cur:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


class FakeConn:
    def __init__(self, row: tuple[Any, ...] | None, fail: bool = False) -> None:
        self.row, self.fail = row, fail

    async def execute(self, sql: str, *a: Any) -> _Cur:
        if self.fail:
            raise ConnectionError("db fora")
        return _Cur(self.row)


class FakePool:
    def __init__(self, row: tuple[Any, ...] | None = (False, False), fail: bool = False) -> None:
        self.conn = FakeConn(row, fail)

    def connection(self) -> "FakePool":
        return self

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, *a: object) -> None:
        return None


@pytest.fixture
def settings(app_secrets: Path) -> AppSettings:
    return AppSettings(dev_login=True)


@pytest.fixture
def golden_file(tmp_path: Path) -> Path:
    p = tmp_path / "golden_login.json"
    p.write_text(json.dumps({"cases": [{"case": "4", "person_key": str(GOLDEN)}]}))
    return p


def make_app(
    settings: AppSettings,
    golden_file: Path,
    redis: PingRedis | None = None,
    pool: FakePool | None = None,
) -> FastAPI:
    app = FastAPI()
    for r in ROUTERS:
        app.include_router(r)
    app.state.settings = settings
    app.state.redis = redis or PingRedis()
    app.state.pool = pool or FakePool()
    app.state.golden_path = golden_file

    @app.post("/whoami-chat")
    async def chat(pk: Annotated[UUID, Depends(get_person_cookie)]) -> dict[str, str]:
        return {"sub": str(pk)}

    @app.post("/whoami-a2a")
    async def a2a(pk: Annotated[UUID, Depends(get_person_a2a)]) -> dict[str, str]:
        return {"sub": str(pk)}

    return app


@pytest.fixture
def client(settings: AppSettings, golden_file: Path) -> Iterator[TestClient]:
    with TestClient(make_app(settings, golden_file), follow_redirects=False) as c:
        yield c


def raw(
    settings: AppSettings,
    aud: str = "chat",
    alg: str = "HS256",
    key: str | None = None,
    exp_delta: timedelta = timedelta(minutes=5),
    sub: str | None = None,
) -> str:
    now = wall_now()
    claims = {"sub": sub or str(GOLDEN), "aud": aud, "iat": now, "exp": now + exp_delta}
    return jwt.encode(claims, key or settings.jwt_secret, algorithm=alg)  # type: ignore[arg-type]


def test_roundtrip_and_ttl(settings: AppSettings) -> None:
    tok = issue_token(GOLDEN, "chat", settings)
    assert verify_token(tok, "chat", settings) == GOLDEN
    c = jwt.decode(tok, options={"verify_signature": False}, audience="chat")
    assert c["exp"] - c["iat"] == settings.limits.jwt_ttl_s == 1800


def test_tampered_401(client: TestClient, settings: AppSettings) -> None:
    tok = issue_token(GOLDEN, "chat", settings)
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    r = client.post("/whoami-chat", cookies={"session": bad})
    assert r.status_code == 401 and bad not in r.text


def test_forged_key_401(client: TestClient, settings: AppSettings) -> None:
    r = client.post("/whoami-chat", cookies={"session": raw(settings, key="x" * 40)})
    assert r.status_code == 401


def test_expired_401(client: TestClient, settings: AppSettings) -> None:
    tok = raw(settings, exp_delta=timedelta(seconds=-5))
    r = client.post("/whoami-chat", cookies={"session": tok})
    assert r.status_code == 401 and tok not in r.text


def test_alg_none_401(client: TestClient, settings: AppSettings) -> None:
    now = wall_now()
    tok = jwt.encode(
        {"sub": str(GOLDEN), "aud": "chat", "exp": now + timedelta(minutes=5)},
        None,
        algorithm="none",
    )
    assert client.post("/whoami-chat", cookies={"session": tok}).status_code == 401


def test_wrong_alg_401(client: TestClient, settings: AppSettings) -> None:
    tok = raw(settings, alg="HS384")
    assert client.post("/whoami-chat", cookies={"session": tok}).status_code == 401


def test_wrong_aud_401(client: TestClient, settings: AppSettings) -> None:
    chat, a2a = issue_token(GOLDEN, "chat", settings), issue_token(GOLDEN, "a2a", settings)
    assert client.post("/whoami-chat", cookies={"session": a2a}).status_code == 401
    assert (
        client.post("/whoami-a2a", headers={"Authorization": f"Bearer {chat}"}).status_code == 401
    )
    assert client.post("/whoami-chat", cookies={"session": chat}).json() == {"sub": str(GOLDEN)}


def test_missing_credentials_401(client: TestClient) -> None:
    assert client.post("/whoami-chat").status_code == 401
    assert client.post("/whoami-a2a").status_code == 401


def test_x_api_key_accepted_for_a2a_only(client: TestClient, settings: AppSettings) -> None:
    a2a = issue_token(GOLDEN, "a2a", settings)
    assert client.post("/whoami-a2a", headers={"x-api-key": a2a}).json() == {"sub": str(GOLDEN)}
    assert client.post("/whoami-a2a", headers={"Authorization": f"Bearer {a2a}"}).status_code == 200
    # no canal chat só vale o cookie
    assert client.post("/whoami-chat", headers={"x-api-key": a2a}).status_code == 401
    chat = issue_token(GOLDEN, "chat", settings)
    assert client.post("/whoami-a2a", headers={"x-api-key": chat}).status_code == 401


def test_body_identity_ignored(client: TestClient, settings: AppSettings) -> None:
    tok = issue_token(GOLDEN, "chat", settings)
    r = client.post(
        "/whoami-chat",
        cookies={"session": tok},
        json={"cpf": "12345678909", "ra": "999", "sub": str(OTHER)},
    )
    assert r.json() == {"sub": str(GOLDEN)}


def test_dev_disabled_404(app_secrets: Path, golden_file: Path) -> None:
    s = AppSettings(dev_login=False)
    with TestClient(make_app(s, golden_file), follow_redirects=False) as c:
        assert c.get("/dev/login", params={"ticket": "x"}).status_code == 404
        body = {"secret": s.dev_login_secret, "case": "4"}
        assert c.post("/dev/login", json=body).status_code == 404
        assert c.post("/dev/a2a-token", json=body).status_code == 404


async def test_ticket_single_use_and_expiry(settings: AppSettings, golden_file: Path) -> None:
    redis = PingRedis()
    app = make_app(settings, golden_file, redis)
    tickets = LoginTickets(redis, settings.limits)
    t = await tickets.issue(GOLDEN)
    with TestClient(app, follow_redirects=False) as c:
        r = c.get("/dev/login", params={"ticket": t})
        assert r.status_code == 303
        assert verify_token(c.cookies["session"], "chat", settings) == GOLDEN
        assert c.get("/dev/login", params={"ticket": t}).status_code == 403  # já usado
        assert c.get("/dev/login", params={"ticket": "inexistente"}).status_code == 403
        # ticket expirado: o Redis o descarta (TTL = login_ticket_ttl_s = 300)
        t2 = await tickets.issue(GOLDEN)
        await redis._r.flushall()
        assert c.get("/dev/login", params={"ticket": t2}).status_code == 403
    assert settings.limits.login_ticket_ttl_s == 300


async def test_ticket_non_golden_403(settings: AppSettings, golden_file: Path) -> None:
    redis = PingRedis()
    t = await LoginTickets(redis, settings.limits).issue(OTHER)
    with TestClient(make_app(settings, golden_file, redis), follow_redirects=False) as c:
        assert c.get("/dev/login", params={"ticket": t}).status_code == 403


def test_wrong_secret_403(client: TestClient) -> None:
    for path in ("/dev/login", "/dev/a2a-token"):
        r = client.post(path, json={"secret": "errado", "case": "4"})
        assert r.status_code == 403 and "errado" not in r.text


def test_non_golden_403(client: TestClient, settings: AppSettings) -> None:
    for path in ("/dev/login", "/dev/a2a-token"):
        r = client.post(path, json={"secret": settings.dev_login_secret, "case": "99"})
        assert r.status_code == 403


def test_dev_login_post_and_a2a_token(client: TestClient, settings: AppSettings) -> None:
    body = {"secret": settings.dev_login_secret, "case": "4"}
    assert client.post("/dev/login", json=body).status_code == 200
    assert verify_token(client.cookies["session"], "chat", settings) == GOLDEN
    r = client.post("/dev/a2a-token", json=body)
    assert verify_token(r.json()["token"], "a2a", settings) == GOLDEN


def test_cookie_flags(client: TestClient, settings: AppSettings) -> None:
    body = {"secret": settings.dev_login_secret, "case": "4"}
    h = client.post("/dev/login", json=body).headers["set-cookie"].lower()
    assert "httponly" in h and "samesite=strict" in h and "path=/" in h
    assert "secure" not in h.replace("samesite", "")


def test_cookie_secure_configurable(app_secrets: Path, golden_file: Path) -> None:
    s = AppSettings(dev_login=True, cookie_secure=True)
    with TestClient(
        make_app(s, golden_file), follow_redirects=False, base_url="https://testserver"
    ) as c:
        h = c.post("/dev/login", json={"secret": s.dev_login_secret, "case": "4"})
    assert "secure" in h.headers["set-cookie"].lower().split("; ")


def test_healthz(settings: AppSettings, golden_file: Path) -> None:
    with TestClient(make_app(settings, golden_file)) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"status": "ok", "db": True, "redis": True}
    with TestClient(make_app(settings, golden_file, redis=PingRedis(False))) as c:
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json() == {
            "status": "degraded",
            "db": True,
            "redis": False,
        }
    with TestClient(make_app(settings, golden_file, pool=FakePool(fail=True))) as c:
        r = c.get("/healthz")
        assert r.status_code == 503 and r.json()["db"] is False


@pytest.mark.parametrize("row", [(True, False), (False, True), None])
async def test_startup_aborts_privileged_role(settings: AppSettings, row: Any) -> None:
    with pytest.raises(RuntimeError, match="privilegiada"):
        await startup_checks(settings, FakePool(row), PingRedis())  # type: ignore[arg-type]


async def test_startup_ok_and_redis_down(settings: AppSettings) -> None:
    await startup_checks(settings, FakePool(), PingRedis())  # type: ignore[arg-type]
    with pytest.raises(ConnectionError):
        await startup_checks(settings, FakePool(), PingRedis(False))  # type: ignore[arg-type]


async def test_startup_aborts_bad_secret(app_secrets: Path) -> None:
    (app_secrets / "jwt_secret").write_text("change-me-change-me-change-me-1")
    with pytest.raises(RuntimeError, match="jwt_secret") as e:
        async with lifespan(FastAPI()):
            pass
    assert "change-me" not in str(e.value)


def test_bind_127() -> None:
    import yaml

    root = Path(__file__).resolve().parents[2]
    c = yaml.safe_load((root / "docker-compose.yml").read_text())
    assert c["services"]["api"]["ports"] == ["127.0.0.1:${API_PORT:-8000}:8000"]
