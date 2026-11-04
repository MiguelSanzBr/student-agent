"""Servidor A2A 1.0 em processo (DefaultRequestHandler + InMemoryTaskStore com dono) e cliente real."""

import logging

import httpx
from a2a.auth.user import User
from a2a.client import ClientConfig, ClientFactory
from a2a.helpers.proto_helpers import new_task_from_user_message, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Role,
    TaskState,
)
from starlette.applications import Starlette

BASE = "http://test"
log = logging.getLogger("spike")


class HeaderUser(User):
    def __init__(self, name: str):
        self._n = name

    @property
    def is_authenticated(self) -> bool:
        return bool(self._n)

    @property
    def user_name(self) -> str:
        return self._n


class Builder(DefaultServerCallContextBuilder):
    boom = False

    def build_user(self, request):
        if Builder.boom:
            raise RuntimeError("segredo-do-builder")
        return HeaderUser(request.headers.get("x-user", ""))


class Exec(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue) -> None:
        text = context.get_user_input()
        msg = context.message
        if context.current_task is None:
            from a2a.server.events import Event  # noqa: F401

            await event_queue.enqueue_event(new_task_from_user_message(msg))
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        if text == "boom":
            raise RuntimeError("segredo-do-executor")
        if text == "reject":
            await u.update_status(
                TaskState.TASK_STATE_REJECTED,
                new_text_message(
                    "Sistema ocupado. Tente de novo em 7 s.",
                    role=Role.ROLE_AGENT,
                    context_id=context.context_id,
                    task_id=context.task_id,
                ),
                metadata={"retry_after_s": 7},
            )
            return
        await u.add_artifact(
            [__import__("a2a.helpers.proto_helpers", fromlist=["x"]).new_text_part(f"eco:{text}")]
        )
        await u.complete()

    async def cancel(self, context, event_queue) -> None:
        u = TaskUpdater(event_queue, context.task_id, context.context_id)
        await u.cancel()


class BoomStore(TaskStore):
    """Delega ao InMemoryTaskStore; levanta quando armado."""

    armed = False

    def __init__(self):
        self.inner = InMemoryTaskStore()

    def _chk(self):
        if BoomStore.armed:
            raise RuntimeError("segredo-do-store")

    async def save(self, task, context):
        self._chk()
        return await self.inner.save(task, context)

    async def get(self, task_id, context):
        self._chk()
        return await self.inner.get(task_id, context)

    async def list(self, params, context: ServerCallContext):
        self._chk()
        return await self.inner.list(params, context)

    async def delete(self, task_id, context):
        self._chk()
        return await self.inner.delete(task_id, context)


def card(streaming=False) -> AgentCard:
    return AgentCard(
        name="spike",
        description="spike",
        version="0.0.1",
        supported_interfaces=[
            AgentInterface(url=f"{BASE}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        capabilities=AgentCapabilities(streaming=streaming, push_notifications=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="s", name="s", description="s", tags=["t"])],
    )


def make_app(store: TaskStore | None = None, streaming=False) -> Starlette:
    c = card(streaming)
    handler = DefaultRequestHandler(Exec(), store or InMemoryTaskStore(), c)
    routes = create_jsonrpc_routes(handler, "/a2a", Builder(), enable_v0_3_compat=False)
    routes += create_agent_card_routes(c)
    return Starlette(routes=routes)


def http(app: Starlette, user: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE, headers={"x-user": user}
    )


async def client_for(app, user: str, streaming=False):
    h = http(app, user)
    return await ClientFactory(ClientConfig(httpx_client=h, streaming=streaming)).create_from_url(
        BASE
    ), h
