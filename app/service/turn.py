"""TurnService concreto: admissão antes do stream, turno via `run_turn` e acerto garantido."""

import asyncio
import dataclasses
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from app.agent.graph import SpendLevel, TurnResult, _bare, run_turn
from app.agent.llm import LLMProvider, Usage
from app.agent.messages import DRAFT_PENDING_USE_BUTTON
from app.config import AppSettings
from app.data.card import build_student_card
from app.data.db import Pool, with_scope
from app.guards.input import InputTooLong, normalize_input
from app.infra.redis import RedisClient
from app.infra.stores.admission import AdmissionBucket, TurnSlots
from app.infra.stores.budget import TokenBudget
from app.infra.stores.drafts import DraftStore
from app.infra.stores.history import HistoryStore
from app.infra.stores.rate_limit import RateLimit
from app.infra.stores.spend import GlobalSpend
from app.infra.stores.turn_outputs import TurnOutputCache
from app.obs.cost import cost_usd
from app.obs.metrics import METRICS
from app.obs.tracing import span
from app.tools.base import ToolContext
from core.clock import monotonic, reference_today
from core.scope import StudentScope

Channel = Literal["web", "a2a"]
Emit = Callable[[Mapping[str, Any]], Awaitable[None]]
ERROR_STATUSES = frozenset({"budget_exceeded", "llm_unavailable", "blocked_limit"})
SLOT_MARGIN_S = 5
RETRY_SLOTS_S = 2
RETRY_SPEND_S = 300
_YES = re.compile(r"(sim|s|ok|pode|pode sim|isso|isso mesmo|claro|quero|confirmo|confirmar|yes)")


@dataclass(frozen=True)
class Admission:
    ok: bool
    status: Literal[200, 429, 503]
    retry_after_s: int | None
    ticket: str | None
    reason: Literal["ok", "rate", "budget", "spend", "quota", "slots"]
    spend_level: SpendLevel = "normal"
    sub: UUID | None = None
    channel: Channel = "web"


class TurnService(Protocol):
    async def admit(self, *, scope: StudentScope, channel: Channel) -> Admission: ...

    async def handle(
        self, *, admission: Admission, scope: StudentScope, conversation_id: str, text: str,
        channel: Channel, emit: Emit,
    ) -> TurnResult: ...  # fmt: skip

    async def settle(
        self, admission: Admission, result: TurnResult | None, *, cancelled: bool = False
    ) -> None: ...


def _is_yes(text: str) -> bool:
    t = unicodedata.normalize("NFKD", text.casefold().strip(" .!?\n\t"))
    return bool(_YES.fullmatch("".join(c for c in t if not unicodedata.combining(c))))


def _tokens(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.thinking_tokens


class DefaultTurnService:
    def __init__(
        self, settings: AppSettings, pool: Pool, redis: RedisClient, llm: LLMProvider
    ) -> None:
        self.settings, self.pool, self.redis, self.llm = settings, pool, redis, llm
        lim = settings.limits
        self.bucket = AdmissionBucket(redis, settings, settings.llm_model)
        self.slots = TurnSlots(redis, settings)
        self.rate, self.budget = RateLimit(redis, lim), TokenBudget(redis, lim)
        self.spend = GlobalSpend(redis, lim)
        self.history, self.drafts = HistoryStore(redis, lim), DraftStore(redis, lim)
        self.outputs = TurnOutputCache(redis)

    async def _refuse(
        self, reason: Literal["rate", "budget", "spend", "quota", "slots"],
        status: Literal[429, 503], wait: int,
    ) -> Admission:  # fmt: skip
        METRICS["agent_admission_rejected_total"].labels(reason=reason).inc()  # type: ignore[union-attr]
        return Admission(False, status, max(1, wait), None, reason)

    async def admit(self, *, scope: StudentScope, channel: Channel) -> Admission:
        sub, adm = scope.person_key, self.settings.slo.admission
        if (wait := await self.rate.hit(sub)) is not None:
            return await self._refuse("rate", 429, wait)
        if (wait := await self.budget.check(sub)) is not None:
            return await self._refuse("budget", 429, wait)
        level = await self.spend.level()
        if level == "blocked":
            return await self._refuse("spend", 503, RETRY_SPEND_S)
        res = await self.bucket.reserve(adm.est_requests, adm.est_tokens)
        if not res.ok or res.ticket is None:
            return await self._refuse("quota", 503, res.retry_after_s or 1)
        ttl = self.settings.slo.graph_budget_s + SLOT_MARGIN_S
        if not await self.slots.acquire(res.ticket, ttl):
            await self.bucket.settle(res.ticket, 0, 0)  # devolve a reserva: nada vai rodar
            return await self._refuse("slots", 503, RETRY_SLOTS_S)
        METRICS["agent_turn_slots_in_use"].inc()  # type: ignore[union-attr]
        return Admission(True, 200, None, res.ticket, "ok", level, sub, channel)

    async def handle(
        self, *, admission: Admission, scope: StudentScope, conversation_id: str, text: str,
        channel: Channel, emit: Emit,
    ) -> TurnResult:  # fmt: skip
        s, sub, trace_id = self.settings, scope.person_key, uuid4().hex
        with span("turn", "AGENT", channel=channel, trace_id=trace_id):
            await emit({"event": "meta", "trace_id": trace_id, "conversation_id": conversation_id})
            try:
                text = normalize_input(text)
            except InputTooLong:
                msg = f"A mensagem passa de {s.limits.input_chars} caracteres. Escreva algo menor."
                await emit({"event": "error", "message": msg})
                return await self._done(_bare("fixed", msg, s.llm_model), emit, trace_id, channel)
            conv = await self.history.load(sub, channel, conversation_id)
            if conv.last_turn_draft_only and _is_yes(text):
                await emit({"event": "answer", "text": DRAFT_PENDING_USE_BUTTON})
                fixed = _bare("fixed", DRAFT_PENDING_USE_BUTTON, s.llm_model)
                return await self._done(fixed, emit, trace_id, channel)
            pending = await self.drafts.pending(sub, conversation_id) if channel == "web" else None
            today = reference_today(s)
            async with with_scope(self.pool, sub, scope.brand_families) as conn:
                card = await build_student_card(conn, scope, s, today=today, pending=pending)

            def ctx_factory(rnd: int, tainted: bool) -> ToolContext:
                return ToolContext(
                    scope, s, channel, conversation_id, tainted, rnd,
                    not conv.other_enrollment_offered, today,
                    lambda: with_scope(self.pool, sub, scope.brand_families), self.drafts,
                )  # fmt: skip

            q: asyncio.Queue[Mapping[str, Any] | None] = asyncio.Queue()

            def writer(ev: Mapping[str, Any]) -> None:  # o grafo é síncrono; o SSE, assíncrono
                if ev.get("event") == "progress":
                    q.put_nowait(ev)

            async def drain() -> None:
                while (ev := await q.get()) is not None:
                    await emit(ev)

            pump = asyncio.create_task(drain())
            try:
                r = await run_turn(
                    llm=self.llm, settings=s, scope=scope, card=card, conversation=conv,
                    text=text, channel=channel, ctx_factory=ctx_factory,
                    deadline=monotonic() + s.slo.graph_budget_s, writer=writer,
                    model=s.llm_model, spend_level=admission.spend_level,
                )  # fmt: skip
            except BaseException:
                pump.cancel()
                raise
            q.put_nowait(None)
            await pump
            if r.draft is not None:
                await emit({"event": "draft", **r.draft.public()})
                METRICS["agent_draft_total"].labels(outcome="exibido").inc()  # type: ignore[union-attr]
            if r.status in ERROR_STATUSES:
                await emit({"event": "error", "message": r.answer})
            elif r.answer:
                await emit({"event": "answer", "text": r.answer})
            if r.new_messages:
                await self.history.append(
                    sub, channel, conversation_id, r.new_messages, draft_only=r.draft_only
                )
            if r.became_tainted:
                await self.history.mark_tainted(sub, channel, conversation_id)
            if r.offered_other_enrollment:
                await self.history.mark_other_enrollment_offered(sub, channel, conversation_id)
            await self.outputs.put(sub, trace_id, self._typed(r))
            if s.app_env == "dev" and s.limits_profile == "eval":
                await emit({"event": "debug", **self._typed(r), "timings_ms": dict(r.timings_ms),
                            "pre_guard_answer": r.pre_guard_answer})  # fmt: skip
            return await self._done(r, emit, trace_id, channel)

    @staticmethod
    def _typed(r: TurnResult) -> dict[str, Any]:
        """Só saídas do servidor (nada do texto do aluno): usadas no 👎 e no debug."""
        return {
            "answer": r.answer, "status": r.status, "guard": r.guard, "answer_kind": r.answer_kind,
            "domain": r.domain, "tools_class": r.tools_class, "model": r.model,
            "tool_calls": [dataclasses.asdict(c) for c in r.tool_calls],
            "records": [dataclasses.asdict(x) for x in r.records],
        }  # fmt: skip

    async def _done(self, r: TurnResult, emit: Emit, trace_id: str, channel: Channel) -> TurnResult:
        m = METRICS
        m["agent_turns_total"].labels(status=r.status, channel=channel).inc()  # type: ignore[union-attr]
        m["agent_turn_seconds"].labels(  # type: ignore[union-attr]
            tools_class=r.tools_class, llm_calls=str(r.llm_calls), guard=r.guard, model=r.model
        ).observe(r.timings_ms.get("total", 0.0) / 1000)
        if r.first_event_ms is not None:
            m["agent_first_event_seconds"].labels(tools_class=r.tools_class).observe(  # type: ignore[union-attr]
                r.first_event_ms / 1000
            )
        m["agent_answer_kind_total"].labels(kind=r.answer_kind, domain=r.domain).inc()  # type: ignore[union-attr]
        m["agent_guard_total"].labels(outcome=r.guard).inc()  # type: ignore[union-attr]
        await emit({"event": "done", "status": r.status, "tools_class": r.tools_class,
                    "llm_calls": r.llm_calls, "guard": r.guard, "model": r.model,
                    "trace_id": trace_id})  # fmt: skip
        return r

    async def settle(
        self, admission: Admission, result: TurnResult | None, *, cancelled: bool = False
    ) -> None:
        if not admission.ok or admission.ticket is None or admission.sub is None:
            return
        adm = self.settings.slo.admission
        try:
            await self.slots.release(admission.ticket)
        finally:
            METRICS["agent_turn_slots_in_use"].dec()  # type: ignore[union-attr]
        if cancelled:
            METRICS["agent_turns_total"].labels(status="cancelled", channel=admission.channel).inc()  # type: ignore[union-attr]
        if result is None:  # cancelado ou erro: o uso real é desconhecido, mantém a estimativa
            await self.bucket.settle(admission.ticket, adm.est_requests, adm.est_tokens)
            return
        await self.bucket.settle(admission.ticket, result.llm_calls, _tokens(result.usage))
        await self.budget.add(admission.sub, _tokens(result.usage))
        u = result.usage
        for kind, n in (("input", u.input_tokens), ("output", u.output_tokens),
                        ("thinking", u.thinking_tokens), ("cached", u.cached_tokens)):  # fmt: skip
            METRICS["agent_tokens_total"].labels(kind=kind).inc(n)  # type: ignore[union-attr]
        try:
            usd = cost_usd(u, result.model, reference_today(self.settings))
        except ValueError:  # modelo sem preço (ex.: fake): não há o que cobrar
            return
        METRICS["agent_cost_usd_total"].inc(float(usd))  # type: ignore[union-attr]
        await self.spend.add(Decimal(usd))
