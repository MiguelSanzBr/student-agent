import os
import stat
from pathlib import Path

import pytest

from scripts.init_secrets import init_secrets

UIDS = {"db": 999, "redis": 999, "app": 10001, "etl": 10001, "evals": 10001}
SHARED = [
    "postgres_password", "etl_owner_password", "agent_app_password", "ops_audit_password",
    "redis_password", "jwt_secret", "dev_login_secret", "hmac_key", "prompt_canary",
]  # fmt: skip


class Chown:
    """chown falso (precisa de root de verdade); verificado de fato no one-shot do Docker."""

    def __init__(self) -> None:
        self.calls: dict[Path, int] = {}

    def __call__(self, path: str | os.PathLike[str], uid: int, gid: int) -> None:
        self.calls[Path(path)] = uid


def run(root: Path, env: Path | None, ch: Chown) -> None:
    init_secrets(root, env, UIDS, chown=ch)


def test_shared_values_equal(tmp_path: Path) -> None:
    run(tmp_path, None, Chown())
    assert (tmp_path / "db/agent_app_password").read_text() == (
        tmp_path / "app/agent_app_password"
    ).read_text()
    assert (tmp_path / "redis/redis_password").read_text() == (
        tmp_path / "app/redis_password"
    ).read_text()
    assert len((tmp_path / "app/hmac_key").read_text()) >= 48


def test_no_secret_leaks_to_wrong_consumer(tmp_path: Path) -> None:
    run(tmp_path, None, Chown())
    assert not (tmp_path / "app/etl_owner_password").exists()
    assert not (tmp_path / "app/ops_audit_password").exists()
    assert not (tmp_path / "app/postgres_password").exists()
    assert not (tmp_path / "redis/jwt_secret").exists()
    assert (tmp_path / "db/ops_audit_password").exists()
    assert (tmp_path / "evals/ops_audit_password").exists()


def test_canary_32_hex(tmp_path: Path) -> None:
    run(tmp_path, None, Chown())
    c = (tmp_path / "app/prompt_canary").read_text()
    assert len(c) == 32 and int(c, 16) >= 0


def test_owner_and_mode_per_consumer(tmp_path: Path) -> None:
    ch = Chown()
    run(tmp_path, None, ch)
    for consumer, uid in UIDS.items():
        files = [p for p in (tmp_path / consumer).iterdir()]
        assert files
        for p in files:
            assert stat.S_IMODE(p.stat().st_mode) == 0o400
            assert ch.calls[p] == uid
        assert ch.calls[tmp_path / consumer] == uid


def test_out_volumes_owned_10001(tmp_path: Path) -> None:
    ch = Chown()
    run(tmp_path, None, ch)
    for d in ("etl-out", "eval-out"):
        assert (tmp_path / d).is_dir()
        assert ch.calls[tmp_path / d] == 10001


def test_idempotent_except_gemini_key(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    root = tmp_path / "v"
    env.write_text("GEMINI_API_KEY=key-one\n")
    run(root, env, Chown())
    first = {n: (root / "app" / n).read_text() for n in ("jwt_secret", "hmac_key")}
    assert (root / "app/gemini_api_key").read_text() == "key-one"
    assert stat.S_IMODE((root / "app/gemini_api_key").stat().st_mode) == 0o400

    env.write_text('GEMINI_API_KEY="key-two"\n')
    run(root, env, Chown())
    assert (root / "app/gemini_api_key").read_text() == "key-two"
    assert {n: (root / "app" / n).read_text() for n in first} == first

    env.write_text("# GEMINI_API_KEY=\nGEMINI_API_KEY=\n")
    run(root, env, Chown())
    assert not (root / "app/gemini_api_key").exists()
    assert {n: (root / "app" / n).read_text() for n in first} == first
    env.unlink()
    run(root, env, Chown())
    assert {n: (root / "app" / n).read_text() for n in first} == first


def test_recovers_partial_volume(tmp_path: Path) -> None:
    run(tmp_path, None, Chown())
    v = (tmp_path / "db/agent_app_password").read_text()
    (tmp_path / "app").chmod(0o700)
    (tmp_path / "app/agent_app_password").unlink()
    run(tmp_path, None, Chown())
    assert (tmp_path / "app/agent_app_password").read_text() == v


@pytest.mark.parametrize("name", SHARED)
def test_each_secret_generated(tmp_path: Path, name: str) -> None:
    run(tmp_path, None, Chown())
    vals = {(tmp_path / c / name).read_text() for c in UIDS if (tmp_path / c / name).exists()}
    assert len(vals) == 1
