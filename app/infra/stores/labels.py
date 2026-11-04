class LabelCache:  # T14
    async def get(self, key: str) -> str | None:
        raise NotImplementedError

    async def put(self, key: str, value: str) -> None:
        raise NotImplementedError
