from collections.abc import Mapping
from typing import Any
from uuid import UUID


class TurnOutputCache:  # T25
    async def put(self, sub: UUID, trace_id: str, payload: Mapping[str, Any]) -> None:
        raise NotImplementedError

    async def get(self, sub: UUID, trace_id: str) -> Mapping[str, Any] | None:
        raise NotImplementedError
