"""TurnService concreto: admissão antes do stream, turno via `run_turn` e acerto garantido."""

import asyncio
import dataclasses
import json
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from app.agent.graph import SpendLevel, TurnResult, bare_result, run_turn
from app.agent.levers import HedgeBudget, Hedger, first_call_delay_s
from app.agent.llm import LLMProvider, LLMResponse, Message, Usage
from app.agent.messages import DRAFT_PENDING_USE_BUTTON
from app.agent.prompt import SYSTEM_PREFIX
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
from app.obs.tracing import current_span, session_id, sha12, span
from app.tools.base import ToolContext, ToolSpec
from app.tools.registry import build_tools
from core.clock import monotonic, reference_today
from core.scope import StudentScope

log = logging.getLogger(__name__)
CONSERVATIVE_MODEL = "gemini-3.8-flash"  # o mais caro da tabela: preço de reserva
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


@cache
def _shas() -> tuple[str, str]:
    """prompt.sha (prefixo, sem o canário) e tools.sha (declarações): versão do agente."""
    specs = [dataclasses.asdict(t.spec) for t in build_tools()]
    return sha12(SYSTEM_PREFIX), sha12(json.dumps(specs, sort_keys=True, default=str))


def _is_yes(text: str) -> bool:
    t = unicodedata.normalize("NFKD", text.casefold().strip(" .!?\n\t"))
    return bool(_YES.fullmatch("".join(c for c in t if not unicodedata.combining(c))))


def _tokens(u: Usage) -> int:
    return u.input_tokens + u.output_tokens + u.thinking_tokens


class _Meter:
    """Acumula o uso real do LLM no turno (sobrevive ao cancelamento) e se o taint já ocorreu."""

    def __init__(self, inner: LLMProvider, hedger: Hedger | None = None) -> None:
        self.inner, self.calls, self.tainted, self.hedger = inner, 0, False, hedger
        self.usage = Usage()
        self.extra_calls, self.extra_usage = 0, Usage()  # 2ª requisição do hedge (cobrada)

    def _count_extra(self) -> None:  # no disparo: o turno pode ser cancelado antes de voltar
        self.extra_calls += 1

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
    ) -> LLMResponse:  # fmt: skip
        first = self.calls == 0
        self.calls += 1  # no disparo: timeout/abort cobra a requisição que ficou em voo

        def call() -> Awaitable[LLMResponse]:
            return self.inner.generate(
                model=model, system=system, messages=messages, tools=tools, deadline=deadline
            )

        if self.hedger is not None and first:
            r, hedged = await self.hedger.run(call, self._count_extra)  # só a 1ª chamada do turno
            if hedged:  # a perdedora cancelada não devolve uso: cobra-se a estimativa (= vencedora)
                e = self.extra_usage
                self.extra_usage = Usage(
                    e.input_tokens + r.usage.input_tokens, e.output_tokens + r.usage.output_tokens,
                    e.thinking_tokens + r.usage.thinking_tokens,
                    e.cached_tokens + r.usage.cached_tokens,
                )  # fmt: skip
        else:
            r = await call()
        u, a = r.usage, self.usage
        self.usage = Usage(
            a.input_tokens + u.input_tokens, a.output_tokens + u.output_tokens,
            a.thinking_tokens + u.thinking_tokens, a.cached_tokens + u.cached_tokens,
        )  # fmt: skip
        return r

    def with_hedge(self, u: Usage, calls: int) -> tuple[Usage, int]:
        e = self.extra_usage  # hedge: a 2ª requisição é real e entra no balde, na cota e no gasto
        return (
            Usage(u.input_tokens + e.input_tokens, u.output_tokens + e.output_tokens,
                  u.thinking_tokens + e.thinking_tokens, u.cached_tokens + e.cached_tokens),
            calls + self.extra_calls,
        )  # fmt: skip


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
        self._meters: dict[str, _Meter] = {}
        self.hedger: Hedger | None = None
        if settings.lever_hedge_first_call:
            h = settings.slo.hedge
            self.hedger = Hedger(
                first_call_delay_s(settings.slo.components, h), HedgeBudget(h), self._saturated
            )
        self._shas = _shas()  # na subida: não pode falhar um turno antes do `meta`

    async def _saturated(self) -> bool:
        return await self.bucket.utilization() > self.settings.slo.hedge.bucket_max_util

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
        self._meters[res.ticket] = _Meter(self.llm, self.hedger)
        return Admission(True, 200, None, res.ticket, "ok", level, sub, channel)

    async def handle(
        self, *, admission: Admission, scope: StudentScope, conversation_id: str, text: str,
        channel: Channel, emit: Emit,
    ) -> TurnResult:  # fmt: skip
        s, sub, trace_id = self.settings, scope.person_key, uuid4().hex
        with span("chat.turn", "AGENT", channel=channel) as sp:
            trace_id = sp.trace_id or trace_id  # o id do trace é o do Phoenix (anotações, 👎)
            prompt_sha, tools_sha = self._shas
            sp.set_attribute("session.id", session_id(s, conversation_id))
            sp.set_attribute("prompt.sha", prompt_sha)
            sp.set_attribute("tools.sha", tools_sha)
            if s.dataset_sha:
                sp.set_attribute("dataset.sha", s.dataset_sha)
            meta = {"event": "meta", "trace_id": trace_id, "conversation_id": conversation_id}
            if s.app_env == "dev" and s.limits_profile == "eval":
                meta["span_id"] = sp.span_id  # o runner da eval anota por span (outro processo)
            await emit(meta)
            try:
                text = normalize_input(text)
                sp.set_content("input.value", text)
            except InputTooLong:
                msg = f"A mensagem passa de {s.limits.input_chars} caracteres. Escreva algo menor."
                await emit({"event": "error", "message": msg})
                return await self._done(
                    bare_result("fixed", msg, s.llm_model), emit, trace_id, channel
                )
            conv = await self.history.load(sub, channel, conversation_id)
            if conv.last_turn_draft_only and _is_yes(text):
                await emit({"event": "answer", "text": DRAFT_PENDING_USE_BUTTON})
                fixed = bare_result("fixed", DRAFT_PENDING_USE_BUTTON, s.llm_model)
                return await self._done(fixed, emit, trace_id, channel)
            pending = await self.drafts.pending(sub, conversation_id) if channel == "web" else None
            today = reference_today(s)
            async with with_scope(self.pool, sub, scope.brand_families) as conn:
                card = await build_student_card(conn, scope, s, today=today, pending=pending)

            meter = self._meters.get(admission.ticket or "") or _Meter(self.llm, self.hedger)

            def ctx_factory(rnd: int, tainted: bool) -> ToolContext:
                meter.tainted = meter.tainted or (tainted and not conv.tainted)
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
                    llm=meter, settings=s, scope=scope, card=card, conversation=conv,
                    text=text, channel=channel, ctx_factory=ctx_factory,
                    deadline=monotonic() + s.slo.graph_budget_s, writer=writer,
                    model=s.llm_model, spend_level=admission.spend_level,
                )  # fmt: skip
            except BaseException:
                pump.cancel()
                if meter.tainted:  # a ferramenta já devolveu texto não confiável: o taint fica
                    await asyncio.shield(self.history.mark_tainted(sub, channel, conversation_id))
                raise
            q.put_nowait(None)
            await pump
            if r.draft is not None:
                if r.became_tainted:  # o taint vale antes de o rascunho chegar ao cliente
                    await asyncio.shield(self.history.mark_tainted(sub, channel, conversation_id))
                await emit({"event": "draft", **r.draft.public()})
                METRICS["agent_draft_total"].labels(outcome="exibido").inc()  # type: ignore[union-attr]
            if r.status in ERROR_STATUSES:
                await emit({"event": "error", "message": r.answer})
            elif r.answer:
                await emit({"event": "answer", "text": r.answer})
            await asyncio.shield(self._persist(sub, channel, conversation_id, r))
            await self.outputs.put(sub, trace_id, self._typed(r))
            if s.app_env == "dev" and s.limits_profile == "eval":
                await emit({"event": "debug", **self._typed(r), "timings_ms": dict(r.timings_ms),
                            "pre_guard_answer": r.pre_guard_answer,
                            "tool_values": self._tool_values(r),
                            "usage": dataclasses.asdict(r.usage),
                            "llm_calls": r.llm_calls})  # fmt: skip
            return await self._done(r, emit, trace_id, channel)

    async def _persist(self, sub: UUID, channel: Channel, cid: str, r: TurnResult) -> None:
        """Flags antes do histórico: uma queda no meio nunca deixa saída de ferramenta sem taint."""
        if r.became_tainted:
            await self.history.mark_tainted(sub, channel, cid)
        if r.offered_other_enrollment:
            await self.history.mark_other_enrollment_offered(sub, channel, cid)
        if r.new_messages:
            await self.history.append(sub, channel, cid, r.new_messages, draft_only=r.draft_only)

    @staticmethod
    def _typed(r: TurnResult) -> dict[str, Any]:
        """Só saídas do servidor (nada do texto do aluno): usadas no 👎 e no debug."""
        return {
            "answer": r.answer, "status": r.status, "guard": r.guard, "answer_kind": r.answer_kind,
            "domain": r.domain, "tools_class": r.tools_class, "model": r.model,
            "tool_calls": [dataclasses.asdict(c) for c in r.tool_calls],
            "records": [dataclasses.asdict(x) for x in r.records],
        }  # fmt: skip

    @staticmethod
    def _tool_values(r: TurnResult) -> dict[str, list[str]]:
        """Só dev+eval: valores dos campos que cada chamada devolveu (causa de erro `dados`)."""
        out: dict[str, list[str]] = {}
        i = 0
        for c in r.tool_calls:  # os registros entram na ordem das chamadas
            n = max(c.returned_records, 0)
            out.setdefault(c.name, []).extend(v for x in r.records[i : i + n]
                                              for v in x.fields.values())  # fmt: skip
            i += n
        return out

    async def _done(self, r: TurnResult, emit: Emit, trace_id: str, channel: Channel) -> TurnResult:
        m = METRICS
        sp = current_span()
        for k, v in {"llm.calls": r.llm_calls, "turn.status": r.status, "guard.outcome": r.guard,
                     "turn.tools_class": r.tools_class, "turn.answer_kind": r.answer_kind,
                     "turn.domain": r.domain, "llm.model_name": r.model}.items():  # fmt: skip
            sp.set_attribute(k, v)
        sp.set_content("output.value", r.answer)
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

    @staticmethod
    async def _safe(what: str, coro: Coroutine[Any, Any, Any]) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 - uma falha não pode pular os outros acertos
            log.exception("settle: %s falhou", what)

    async def settle(
        self, admission: Admission, result: TurnResult | None, *, cancelled: bool = False
    ) -> None:
        if not admission.ok or admission.ticket is None or admission.sub is None:
            return
        s, adm = self.settings, self.settings.slo.admission
        meter = self._meters.pop(admission.ticket, None)
        if result is not None:
            u, calls, model = result.usage, result.llm_calls, result.model
            if meter is not None:
                u, calls = meter.with_hedge(u, calls)
        else:  # cancelado ou erro: vale o uso medido, no mínimo a estimativa da admissão
            u, calls, model = (
                (*meter.with_hedge(meter.usage, meter.calls), s.llm_model)
                if meter
                else (Usage(), 0, s.llm_model)
            )
            # sem nenhuma chamada ao LLM (erro antes dela) não há o que estimar: só a reserva volta
            if meter is None or (calls and _tokens(u) < adm.est_tokens):
                u, calls = Usage(output_tokens=adm.est_tokens), max(calls, adm.est_requests)
        await self._safe("vaga", self.slots.release(admission.ticket))
        METRICS["agent_turn_slots_in_use"].dec()  # type: ignore[union-attr]
        if cancelled:
            METRICS["agent_turns_total"].labels(status="cancelled", channel=admission.channel).inc()  # type: ignore[union-attr]
        await self._safe("balde", self.bucket.settle(admission.ticket, calls, _tokens(u)))
        await self._safe("cota diária", self.budget.add(admission.sub, _tokens(u)))
        for kind, n in (("input", u.input_tokens), ("output", u.output_tokens),
                        ("thinking", u.thinking_tokens), ("cached", u.cached_tokens)):  # fmt: skip
            METRICS["agent_tokens_total"].labels(kind=kind).inc(n)  # type: ignore[union-attr]
        await self._safe("gasto global", self._charge(u, model))

    async def _charge(self, u: Usage, model: str) -> None:
        on = reference_today(self.settings)
        if model == "demo-replay":  # replay não tem preço nem custa nada
            return
        if model == "loadtest":  # rótulo do LLM de carga: cobra como o modelo configurado
            model = self.settings.llm_model
        try:
            usd = cost_usd(u, model, on)
        except ValueError:
            if self.settings.llm_provider == "fake":  # fake não tem preço
                return
            log.warning("sem preço para %s: cobrando pelo preço conservador", model)
            usd = cost_usd(u, CONSERVATIVE_MODEL, on)
        METRICS["agent_cost_usd_total"].inc(float(usd))  # type: ignore[union-attr]
        await self.spend.add(Decimal(usd))
