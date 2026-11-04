from collections.abc import Sequence
from typing import Any, Protocol

import redis.asyncio as aioredis

from core.rkey import RKey

# GET+DEL atômico (uso único); passe por `eval` com 1 chave e sem args.
GETDEL = """
local v = redis.call('GET', KEYS[1])
if v then redis.call('DEL', KEYS[1]) end
return v
"""


class RedisClient(Protocol):
    """Aceita só RKey; o mypy rejeita str."""

    async def get(self, key: RKey) -> bytes | None: ...
    async def set(
        self, key: RKey, value: bytes, *, ex: int | None = None, nx: bool = False
    ) -> bool: ...
    async def delete(self, key: RKey) -> int: ...
    async def eval(
        self, script: str, keys: Sequence[RKey], args: Sequence[str | int | float]
    ) -> Any: ...


class RealRedis:
    """Adapta redis.asyncio ao protocolo RedisClient (eval com listas) e expõe ping."""

    def __init__(self, host: str, port: int, password: str) -> None:
        self._r = aioredis.Redis(host=host, port=port, password=password)

    async def ping(self) -> bool:
        return bool(await self._r.ping())

    async def get(self, key: RKey) -> bytes | None:
        return await self._r.get(key)  # type: ignore[return-value]

    async def set(
        self, key: RKey, value: bytes, *, ex: int | None = None, nx: bool = False
    ) -> bool:
        return bool(await self._r.set(key, value, ex=ex, nx=nx))

    async def delete(self, key: RKey) -> int:
        return int(await self._r.delete(key))

    async def eval(
        self, script: str, keys: Sequence[RKey], args: Sequence[str | int | float]
    ) -> Any:
        return await self._r.eval(script, len(keys), *keys, *args)

    async def aclose(self) -> None:
        await self._r.aclose()
