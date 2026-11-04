import os

os.environ.setdefault("OTEL_INSTRUMENTATION_A2A_SDK_ENABLED", "false")

from pathlib import Path

import pytest

GOOD = {
    "jwt_secret": "j" * 40,
    "dev_login_secret": "d" * 40,
    "hmac_key": "h" * 48,
    "prompt_canary": "0123456789abcdef0123456789abcdef",
    "agent_app_password": "a" * 40,
    "redis_password": "r" * 40,
}


@pytest.fixture
def app_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Diretório de segredos do app válido, já apontado pelo AppSettings."""
    from app.config import AppSettings

    d = tmp_path / "app-secrets"
    d.mkdir()
    for k, v in GOOD.items():
        (d / k).write_text(v)
    monkeypatch.setitem(AppSettings.model_config, "secrets_dir", str(d))
    return d
