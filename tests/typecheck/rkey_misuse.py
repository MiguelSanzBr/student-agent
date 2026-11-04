from app.infra.redis import RedisClient


async def misuse(client: RedisClient) -> None:
    await client.get("sa:conv:x")
