from collections.abc import Sequence
from typing import Any

import fakeredis.aioredis as fr

from core.rkey import RKey


class FakeRedis:
    """Adapta o fakeredis ao protocolo RedisClient (eval com listas)."""

    def __init__(self) -> None:
        self._r = fr.FakeRedis()

    async def get(self, key: RKey) -> bytes | None:
        return await self._r.get(key)  # type: ignore[no-any-return]

    async def set(
        self, key: RKey, value: bytes, *, ex: int | None = None, nx: bool = False
    ) -> bool:
        return bool(await self._r.set(key, value, ex=ex, nx=nx))

    async def delete(self, key: RKey) -> int:
        return await self._r.delete(key)  # type: ignore[no-any-return]

    async def eval(
        self, script: str, keys: Sequence[RKey], args: Sequence[str | int | float]
    ) -> Any:
        return await self._r.eval(script, len(keys), *keys, *args)  # type: ignore[no-untyped-call]
