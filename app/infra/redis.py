from collections.abc import Sequence
from typing import Any, Protocol

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
