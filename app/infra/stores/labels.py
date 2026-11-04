from app.infra.redis import RedisClient
from core.rkey import rkey


class LabelCache:
    """Rótulos de catálogo (ex.: nome do curso); TTL de 1 h: o catálogo é recriado a cada carga."""

    def __init__(self, redis: RedisClient) -> None:
        self._r = redis

    async def get(self, key: str) -> str | None:
        v = await self._r.get(rkey("labels", "global", "-", key))
        return None if v is None else v.decode()

    async def put(self, key: str, value: str) -> None:
        await self._r.set(rkey("labels", "global", "-", key), value.encode(), ex=3600)
