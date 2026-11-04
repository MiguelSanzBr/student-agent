"""`python -m scripts.login_url CASO`: cria um ticket de uso único e imprime a URL (dev)."""

import asyncio
import os
import sys

from pydantic import ValidationError

from app.api.dev import load_golden
from app.config import AppSettings
from app.infra.redis import RealRedis
from app.infra.stores.tickets import LoginTickets


async def main(case: str) -> str:
    try:
        s = AppSettings()
    except ValidationError as e:  # o pydantic ecoaria o valor do segredo: só os nomes dos campos
        campos = ", ".join(sorted({str(x["loc"][0]) for x in e.errors() if x["loc"]}))
        raise SystemExit(f"configuração inválida: {campos}") from None
    if not s.dev_login:
        raise SystemExit("DEV_LOGIN desligado")
    try:
        pk = load_golden(s.etl_out_dir / "golden_login.json").get(case)
    except FileNotFoundError as e:
        raise SystemExit(str(e)) from None
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
