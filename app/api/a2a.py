"""A2A 1.0 (JSON-RPC) sobre o mesmo TurnService. Posse por dono: toda tarefa e todo contexto
são escopados pelo `sub` do JWT; exceções nunca vazam a mensagem original."""

import asyncio
import logging
import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from a2a.auth.user import User
from a2a.helpers.proto_helpers import new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.agent_card_routes import create_agent_card_routes
from a2a.server.routes.common import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.tasks import TaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    ListTasksRequest,
    ListTasksResponse,
    Role,
    SecurityRequirement,
    SecurityScheme,
    StringList,
    Task,
    TaskState,
)
from a2a.utils.errors import InternalError, InvalidParamsError
from fastapi import APIRouter, HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.api.auth import get_person_a2a
from app.config import AppSettings
from app.data.db import load_scope, with_scope
from app.infra.stores import CONV_ID_PATTERN
from app.infra.stores.a2a_tasks import RedisTaskStore
from app.infra.stores.labels import LabelCache
from app.service.turn import TurnService
from core.scope import StudentScope

_a2a_log = logging.getLogger("a2a")  # o SDK loga str(e) das exceções: silencia tudo
_a2a_log.setLevel(logging.CRITICAL + 1)
_a2a_log.propagate = False

log = logging.getLogger(__name__)
router = APIRouter()
INTERNAL = "Erro interno."
FAILED_TEXT = "Não foi possível concluir agora."
NO_ENROLLMENT_TEXT = "Não encontrei matrícula ativa para este acesso."
TEXT_ONLY = "Envie uma única mensagem de texto."


def build_agent_card(settings: AppSettings) -> AgentCard:
    url = settings.a2a_public_url.rstrip("/")
    return AgentCard(
        name="Atendimento ao aluno",
        description=(
            "Responde dúvidas financeiras e acadêmicas do aluno autenticado. Somente leitura: "
            "solicitações exigem confirmação humana no portal."
        ),
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(url=f"{url}/a2a", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        capabilities=AgentCapabilities(
            streaming=False, push_notifications=False, extended_agent_card=False
        ),
        security_schemes={
            "bearer": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="bearer", bearer_format="JWT"
                )
            ),
            "apiKey": SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(location="header", name="x-api-key")
            ),
        },
        security_requirements=[
            SecurityRequirement(schemes={"bearer": StringList()}),
            SecurityRequirement(schemes={"apiKey": StringList()}),
        ],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="atendimento-aluno",
                name="Atendimento ao aluno",
                description="Financeiro, notas, frequência, protocolos e AVA do aluno.",
                tags=["aluno", "financeiro", "acadêmico"],
                examples=["Quanto falta para quitar meu curso?"],
            )
        ],
    )


class _Caller(User):
    def __init__(self, sub: UUID) -> None:
        self._sub = sub

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return str(self._sub)


class JwtContextBuilder(DefaultServerCallContextBuilder):
    """Bearer ou x-api-key (aud=a2a). Sem credencial válida: HTTP 401; qualquer outra falha:
    mensagem fixa."""

    def build_user(self, request: Request) -> User:
        try:
            return _Caller(get_person_a2a(request))
        except HTTPException:
            raise
        except Exception as e:
            log.error("context builder falhou: %s", type(e).__name__)
            raise InternalError(INTERNAL) from None


async def _load_scope(state: Any, pk: UUID) -> StudentScope:
    async with with_scope(state.pool, pk, ()) as conn:
        return await load_scope(conn, pk, LabelCache(state.redis))


class StudentAgentExecutor(AgentExecutor):
    def __init__(self, state: Any) -> None:
        self.state = state

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        if not all(
            re.fullmatch(CONV_ID_PATTERN, i) for i in (context.context_id, context.task_id) if i
        ):
            raise InvalidParamsError("contextId/taskId inválido")
        msg = context.message
        assert msg is not None  # noqa: S101  (o SDK só chama execute com mensagem)
        if context.current_task is None:
            await event_queue.enqueue_event(new_task_from_user_message(msg))
        upd = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")

        async def say(state: TaskState, text: str, meta: Mapping[str, Any] | None = None) -> None:
            m = new_text_message(
                text, role=Role.ROLE_AGENT, context_id=upd.context_id, task_id=upd.task_id
            )
            await upd.update_status(state, m, metadata=dict(meta) if meta else None)

        if len(msg.parts) != 1 or not msg.parts[0].HasField("text"):
            return await say(TaskState.TASK_STATE_FAILED, TEXT_ONLY)
        sub = UUID(context.call_context.user.user_name)
        svc: TurnService = self.state.turns
        adm = None
        result = None
        cancelled = False
        try:
            scope = await _load_scope(self.state, sub)
            if not scope.enrollments:
                return await say(TaskState.TASK_STATE_FAILED, NO_ENROLLMENT_TEXT)
            adm = await svc.admit(scope=scope, channel="a2a")
            if not adm.ok:
                n = adm.retry_after_s or 1
                return await say(
                    TaskState.TASK_STATE_REJECTED,
                    f"Sistema ocupado. Tente de novo em {n} s.", {"retry_after_s": n},
                )  # fmt: skip
            answers: list[str] = []

            async def emit(ev: Mapping[str, Any]) -> None:
                if ev.get("event") == "answer":
                    answers.append(str(ev["text"]))

            # o HistoryStore já escopa por sub+canal: o contextId alheio cai num histórico vazio
            result = await svc.handle(
                admission=adm, scope=scope, conversation_id=upd.context_id,
                text=msg.parts[0].text, channel="a2a", emit=emit,
            )  # fmt: skip
            if result.status in {"budget_exceeded", "llm_unavailable", "blocked_limit"}:
                return await say(TaskState.TASK_STATE_FAILED, result.answer or FAILED_TEXT)
            await upd.add_artifact([new_text_part("\n".join(answers) or result.answer)])
            await upd.complete()
        except BaseException as e:
            cancelled = not isinstance(e, Exception)
            if cancelled:
                raise
            log.error("turno A2A falhou: %s", type(e).__name__)
            await say(TaskState.TASK_STATE_FAILED, FAILED_TEXT)
        finally:
            if adm is not None:
                try:
                    await asyncio.shield(svc.settle(adm, result, cancelled=cancelled))
                except Exception as e:
                    log.error("settle A2A falhou: %s", type(e).__name__)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await TaskUpdater(event_queue, context.task_id or "", context.context_id or "").cancel()


class SafeTaskStore(TaskStore):
    """Qualquer falha do store vira InternalError com mensagem fixa (o SDK vazaria str(e))."""

    def __init__(self, inner: TaskStore) -> None:
        self.inner = inner

    @staticmethod
    def _fail(e: Exception) -> InternalError:
        log.error("task store falhou: %s", type(e).__name__)  # nunca a mensagem
        return InternalError(INTERNAL)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        try:
            await self.inner.save(task, context)
        except Exception as e:
            raise self._fail(e) from None

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        try:
            return await self.inner.get(task_id, context)
        except Exception as e:
            raise self._fail(e) from None

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        try:
            await self.inner.delete(task_id, context)
        except Exception as e:
            raise self._fail(e) from None

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        try:
            return await self.inner.list(params, context)
        except Exception as e:
            raise self._fail(e) from None


def _build(state: Any) -> tuple[Any, Any]:
    card = build_agent_card(state.settings)
    store = SafeTaskStore(RedisTaskStore(state.redis, state.settings.limits.a2a_task_ttl_s))
    handler = DefaultRequestHandler(StudentAgentExecutor(state), store, card)
    rpc = create_jsonrpc_routes(handler, "/a2a", JwtContextBuilder(), enable_v0_3_compat=False)
    return rpc[0].endpoint, create_agent_card_routes(card)[0].endpoint


def _endpoints(request: Request) -> tuple[Any, Any]:
    """O handler precisa do estado de runtime (Redis, TurnService): monta na 1ª chamada."""
    st = request.app.state
    if getattr(st, "a2a_endpoints", None) is None:
        st.a2a_endpoints = _build(st)
    return st.a2a_endpoints  # type: ignore[no-any-return]


@router.post("/a2a", response_model=None)
async def a2a(request: Request) -> Response:
    return await _endpoints(request)[0](request)  # type: ignore[no-any-return]


@router.get("/.well-known/agent-card.json", response_model=None)
async def agent_card(request: Request) -> Response:
    return await _endpoints(request)[1](request)  # type: ignore[no-any-return]
