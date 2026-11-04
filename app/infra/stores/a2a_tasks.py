"""TaskStore do A2A no Redis: toda chave e todo índice são do dono (sub do JWT), com TTL."""

import re
from uuid import UUID

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task, TaskState

from app.infra.redis import RedisClient
from app.infra.stores import CONV_ID_PATTERN
from core.clock import wall_now
from core.rkey import RKey, rkey

DEFAULT_PAGE = 50
# o índice (ZSET id -> instante do save) permite listar sem SCAN; o dono já está na chave
_INDEX = "i"  # as tarefas usam o prefixo "t:": nenhum id de cliente alcança o índice
REJECTED_TTL_S = 60  # tarefa recusada na admissão não fica 1 h no Redis
_ADD = "redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1]); redis.call('EXPIRE', KEYS[1], ARGV[3])"
_REM = "return redis.call('ZREM', KEYS[1], ARGV[1])"
_IDS = "return redis.call('ZRANGE', KEYS[1], 0, -1)"


class RedisTaskStore(TaskStore):
    def __init__(self, redis: RedisClient, ttl_s: int) -> None:
        self.redis, self.ttl = redis, ttl_s

    @staticmethod
    def _owner(context: ServerCallContext) -> UUID:
        return UUID(resolve_user_scope(context))  # sem dono válido: levanta, nunca lê de outro

    def _key(self, owner: UUID, task_id: str) -> RKey:
        return rkey("a2a_task", owner, "a2a", f"t:{task_id}")

    @staticmethod
    def _idx(owner: UUID) -> RKey:
        return rkey("a2a_task", owner, "a2a", _INDEX)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        o = self._owner(context)
        ttl = REJECTED_TTL_S if task.status.state == TaskState.TASK_STATE_REJECTED else self.ttl
        ttl = min(ttl, self.ttl)
        await self.redis.set(self._key(o, task.id), task.SerializeToString(), ex=ttl)
        await self.redis.eval(_ADD, [self._idx(o)], [task.id, wall_now().timestamp(), self.ttl])

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        if not re.fullmatch(CONV_ID_PATTERN, task_id):  # id de cliente fora do formato: não existe
            return None
        raw = await self.redis.get(self._key(self._owner(context), task_id))
        return Task.FromString(raw) if raw else None

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        o = self._owner(context)
        await self.redis.delete(self._key(o, task_id))
        await self.redis.eval(_REM, [self._idx(o)], [task_id])

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        o = self._owner(context)
        ids = await self.redis.eval(_IDS, [self._idx(o)], [])
        tasks: list[Task] = []
        for raw_id in reversed(ids or []):  # mais recentes primeiro
            tid = raw_id.decode() if isinstance(raw_id, bytes) else str(raw_id)
            t = await self.get(tid, context)
            if t is None:  # expirou: sai do índice
                await self.redis.eval(_REM, [self._idx(o)], [tid])
            elif (not params.context_id or t.context_id == params.context_id) and (
                not params.status or t.status.state == params.status
            ):
                tasks.append(t)
        size = params.page_size or DEFAULT_PAGE
        # shortcut: sem paginação por token (TTL de 1 h e poucas tarefas por aluno)
        return ListTasksResponse(
            tasks=tasks[:size], next_page_token="", page_size=size, total_size=len(tasks)
        )
