"""One-shot do compose: gera os segredos uma vez e distribui um volume por consumidor."""

import os
import secrets
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

# quem lê o quê: cada volume só recebe o que o consumidor precisa
CONSUMERS: dict[str, tuple[str, ...]] = {
    "db": ("postgres_password", "etl_owner_password", "agent_app_password", "ops_audit_password"),
    "redis": ("redis_password",),
    "app": (
        "agent_app_password",
        "redis_password",
        "jwt_secret",
        "dev_login_secret",
        "hmac_key",
        "prompt_canary",
    ),  # fmt: skip
    "etl": ("etl_owner_password", "hmac_key"),
    "evals": (
        "agent_app_password",
        "ops_audit_password",
        "redis_password",
        "jwt_secret",
        "dev_login_secret",
        "hmac_key",
        "prompt_canary",
    ),  # fmt: skip
}
OUT_VOLUMES = {"etl-out": "etl", "eval-out": "evals"}  # volume -> consumidor que define o dono
# .env -> arquivo de segredo do app
LLM_KEYS = {
    "GEMINI_API_KEY": "gemini_api_key",
    "ANTHROPIC_API_KEY": "anthropic_api_key",
    "OPENAI_API_KEY": "openai_api_key",
}

Chown = Callable[[str, int, int], None]


def _generate(name: str) -> str:
    return secrets.token_hex(16) if name == "prompt_canary" else secrets.token_urlsafe(48)


def _write(path: Path, value: str, uid: int, chown: Chown) -> None:
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "w") as f:
        f.write(value)
    chown(str(path), uid, uid)
    path.chmod(0o400)


def _env_key(env_file: Path | None, name: str) -> str | None:
    if env_file is None or not env_file.is_file():
        return None
    for line in env_file.read_text().splitlines():
        key, sep, val = line.strip().partition("=")
        if sep and key.strip() == name:
            return val.strip().strip("'\"") or None
    return None


def init_secrets(
    root: Path, env_file: Path | None, uids: Mapping[str, int], chown: Chown = os.chown
) -> None:
    for consumer, names in CONSUMERS.items():
        d = root / consumer
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)  # só durante a escrita
        for name in names:
            if not (d / name).exists():
                # reaproveita a cópia de outro consumidor (volume parcial) antes de gerar
                known = next(
                    (
                        (root / c / name).read_text()
                        for c in CONSUMERS
                        if (root / c / name).exists()
                    ),
                    None,
                )
                _write(d / name, known or _generate(name), uids[consumer], chown)
        d.chmod(0o500)
        chown(str(d), uids[consumer], uids[consumer])
    for out, owner in OUT_VOLUMES.items():
        (root / out).mkdir(parents=True, exist_ok=True)
        chown(str(root / out), uids[owner], uids[owner])
    # única parte não idempotente: as chaves de LLM seguem o .env (cria, troca ou apaga)
    app = root / "app"
    for env_name, secret in LLM_KEYS.items():
        target = app / secret
        key = _env_key(env_file, env_name)
        if (key is None and target.exists()) or (key and not _same(target, key)):
            app.chmod(0o700)
            if key is None:
                target.unlink()
            else:
                _write(target, key, uids["app"], chown)
            app.chmod(0o500)


def _same(path: Path, value: str) -> bool:
    return path.exists() and path.read_text() == value


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/out")
    env = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    init_secrets(out, env, {"db": 999, "redis": 999, "app": 10001, "etl": 10001, "evals": 10001})
