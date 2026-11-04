from typing import Any

from a2a.server.tasks import TaskStore


class RedisTaskStore(TaskStore):  # T27
    async def get(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def save(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError

    async def list(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError
