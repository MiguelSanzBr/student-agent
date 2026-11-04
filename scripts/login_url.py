"""`python -m scripts.login_url CASO`: cria um ticket de uso único e imprime a URL (dev)."""

import asyncio
import os
import sys

from app.api.dev import load_golden
from app.config import AppSettings
from app.infra.redis import RealRedis
from app.infra.stores.tickets import LoginTickets


async def main(case: str) -> str:
    s = AppSettings()
    if not s.dev_login:
        raise SystemExit("DEV_LOGIN desligado")
    pk = load_golden(s.etl_out_dir / "golden_login.json").get(case)
    if pk is None:
        raise SystemExit(f"caso {case!r} não é golden")
    redis = RealRedis(s.redis_host, s.redis_port, s.redis_password)
    try:
        ticket = await LoginTickets(redis, s.limits).issue(pk)
    finally:
        await redis.aclose()
    base = os.environ.get("PUBLIC_BASE_URL", f"http://localhost:{s.api_port}")
    return f"{base}/dev/login?ticket={ticket}"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("uso: python -m scripts.login_url CASO")
    print(asyncio.run(main(sys.argv[1])))
