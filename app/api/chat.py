"""POST /chat (SSE) e GET /me. A admissão roda antes de abrir o stream (503/429 + Retry-After)."""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent.messages import LLM_UNAVAILABLE_TEXT
from app.api.auth import get_person_cookie
from app.data.db import load_scope, with_scope
from app.infra.stores.labels import LabelCache
from app.obs.metrics import METRICS
from app.service.turn import Admission, TurnService
from core.scope import StudentScope

router = APIRouter()
log = logging.getLogger(__name__)
HEARTBEAT_S = 15.0
MAX_BODY_CHARS = 20_000  # o teto de 2000 é do guard (erro em PT-BR); isto só barra o absurdo
REFUSALS = {
    "rate": "Você enviou mensagens demais. Tente de novo em instantes.",
    "budget": "Você atingiu o limite diário de uso. Volte amanhã.",
    "spend": "Estamos com muitos atendimentos agora. Tente de novo em instantes.",
    "quota": "Estamos com muitos atendimentos agora. Tente de novo em instantes.",
    "slots": "Estamos com muitos atendimentos agora. Tente de novo em instantes.",
}


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(max_length=MAX_BODY_CHARS)


async def _load_scope(request: Request, pk: UUID) -> StudentScope:
    st = request.app.state
    async with with_scope(st.pool, pk, ()) as conn:
        return await load_scope(conn, pk, LabelCache(st.redis))


async def _first_name(request: Request, scope: StudentScope) -> str:
    async with with_scope(request.app.state.pool, scope.person_key, scope.brand_families) as conn:
        cur = await conn.execute(
            "SELECT first_name FROM canon.person WHERE person_key = %s LIMIT 1",
            (scope.person_key,),
        )
        row = await cur.fetchone()
    return str(row[0]) if row and row[0] else ""


async def _scope(request: Request, pk: Annotated[UUID, Depends(get_person_cookie)]) -> StudentScope:
    scope = await _load_scope(request, pk)
    if not scope.enrollments:
        raise HTTPException(status_code=403, detail="sem matrícula")
    return scope


def _sse(name: str, data: Mapping[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


class _Settled(StreamingResponse):
    """Garante o `settle` (vaga e balde) por qualquer caminho, inclusive cliente que cai antes do
    1º byte, quando o gerador nunca chega a rodar."""

    def __init__(self, content: AsyncIterator[bytes], svc: TurnService, adm: Admission) -> None:
        hdr = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        super().__init__(content, media_type="text/event-stream", headers=hdr)
        self.svc, self.adm = svc, adm
        self.result: Any = None
        self.cancelled = False
        self._done = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(self.settle())

    async def settle(self) -> None:
        if not self._done:
            self._done = True
            await self.svc.settle(self.adm, self.result, cancelled=self.cancelled)


@router.post("/chat", response_model=None)
async def chat(
    body: ChatRequest, request: Request, scope: Annotated[StudentScope, Depends(_scope)]
) -> StreamingResponse | JSONResponse:
    svc: TurnService = request.app.state.turns
    adm = await svc.admit(scope=scope, channel="web")
    if not adm.ok:
        return JSONResponse(
            {"detail": REFUSALS[adm.reason], "retry_after_s": adm.retry_after_s},
            status_code=adm.status, headers={"Retry-After": str(adm.retry_after_s)},
        )  # fmt: skip
    q: asyncio.Queue[tuple[str, Mapping[str, Any]] | None] = asyncio.Queue()
    holder: list[_Settled] = []

    async def emit(ev: Mapping[str, Any]) -> None:
        await q.put((str(ev["event"]), {k: v for k, v in ev.items() if k != "event"}))

    async def run() -> None:
        try:
            holder[0].result = await svc.handle(
                admission=adm, scope=scope, conversation_id=body.conversation_id, text=body.text,
                channel="web", emit=emit,
            )  # fmt: skip
        except Exception:
            log.exception("turno falhou")  # o detalhe fica no log; o cliente só vê a mensagem fixa
            METRICS["agent_turns_total"].labels(status="error", channel="web").inc()  # type: ignore[union-attr]
            await emit({"event": "error", "message": LLM_UNAVAILABLE_TEXT})
            await emit({"event": "done", "status": "error"})
        finally:
            await q.put(None)

    async def stream() -> AsyncIterator[bytes]:
        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), HEARTBEAT_S)
                except TimeoutError:
                    yield _sse("ping", {})
                    continue
                if item is None:
                    break
                yield _sse(*item)
        finally:
            if not task.done():
                holder[0].cancelled = True
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    resp = _Settled(stream(), svc, adm)
    holder.append(resp)
    return resp


@router.get("/me")
async def me(request: Request, scope: Annotated[StudentScope, Depends(_scope)]) -> dict[str, Any]:
    st = request.app.state
    suggested = getattr(st.turns.llm, "suggested", None)
    return {
        "first_name": await _first_name(request, scope),
        "enrollments": [e.label for e in scope.enrollments],
        "demo_mode": callable(suggested),  # só o LLM de replay sugere perguntas
        "suggested": list(suggested()) if callable(suggested) else [],
    }
