"""Grafo do turno (LangGraph): LLM -> ferramentas (<= rodadas) -> guard -> resposta (spec §6)."""

import asyncio
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.llm import LLMProvider, LLMUnavailable, Message, ToolCall, Usage
from app.agent.messages import (
    BLOCKED_TEXT,
    BUDGET_EXCEEDED_TEXT,
    DRAFT_SHOWN_TEMPLATE,
    FERRAMENTA_INDISPONIVEL,
    LLM_UNAVAILABLE_TEXT,
    REST_INSTRUCTION,
    TAINT_NO_DRAFT,
    card_messages,
    wrap_tool_result,
)
from app.agent.prompt import REFUSAL_PREFIX, SYSTEM_PREFIX, system_prompt
from app.config import AppSettings
from app.data.card import StudentCard
from app.guards.evidence import check_evidence
from app.guards.fallback import degrade, strip_markdown_and_codes, template_fallback
from app.guards.types import GuardResult
from app.infra.stores.history import Conversation
from app.tools.base import (
    Domain,
    DraftCard,
    EntityRecord,
    Tool,
    ToolCallTrace,
    ToolContext,
    ToolError,
    ToolOutput,
    default_ontology,
    validate_args,
)
from app.tools.registry import build_tools
from core.clock import monotonic
from core.ontology import Ontology
from core.scope import StudentScope

TurnStatus = Literal["ok", "degraded", "fallback", "fixed", "blocked_limit", "shed",
                     "budget_exceeded", "llm_unavailable", "cancelled"]  # fmt: skip
AnswerKind = Literal["resposta", "recusa", "vazio", "fora_cobertura", "transbordo"]


@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: TurnStatus
    guard: Literal["pass", "retry_pass", "degraded", "fallback", "n/a"]
    draft: DraftCard | None
    tool_calls: tuple[ToolCallTrace, ...]
    tools_class: Literal["card", "1", "2+"]  # 0, 1 ou ≥ 2 ferramentas executadas
    llm_calls: int
    model: str
    answer_kind: AnswerKind
    domain: Domain | Literal["cartao", "geral"]
    draft_only: bool
    usage: Usage
    timings_ms: Mapping[str, float]
    first_event_ms: float | None
    pre_guard_answer: str | None
    new_messages: tuple[Message, ...]
    records: tuple[EntityRecord, ...]
    became_tainted: bool = False  # T25 chama HistoryStore.mark_tainted
    offered_other_enrollment: bool = False  # T25 chama mark_other_enrollment_offered
    cost_degraded: bool = False


log = logging.getLogger(__name__)
EMPTY = "Nenhum registro encontrado."
TRANSBORDO = frozenset({"GEN-090", "OUV-099"})
COMPACT_CHARS = 1200  # ~300 tokens
# o texto de `coverage_note` (app/rules/coverage.py), onde quer que a ferramenta o ponha
_COVERAGE = re.compile(r"Não há dados .+? antes de \d{2}/\d{2}/\d{4}")
GuardOutcome = Literal["pass", "retry_pass", "degraded", "fallback", "n/a"]
_PROGRESS = {"event": "progress", "fase": "consultando", "texto": "Consultando seus dados..."}
SpendLevel = Literal["normal", "degraded", "blocked"]


@dataclass(frozen=True)
class _Env:
    llm: LLMProvider
    settings: AppSettings
    tools: Mapping[str, Tool]
    system: str
    question: str
    scope: StudentScope
    card: StudentCard
    channel: Literal["web", "a2a"]
    ctx_factory: Callable[[int, bool], ToolContext]
    deadline: float
    model: str
    turn: int
    max_rounds: int
    allow_retry: bool
    ontology: Ontology
    system_notes: tuple[str, ...]


class _S(TypedDict, total=False):
    msgs: list[Message]  # o que o LLM vê: cartão + histórico + turno
    new: list[Message]  # o que o turno acrescenta ao histórico
    rnd: int
    llm_calls: int
    usage: Usage
    text: str | None
    calls: tuple[ToolCall, ...]
    meta: bytes | None
    model: str
    trace: list[ToolCallTrace]
    records: list[EntityRecord]
    tainted: bool
    draft: DraftCard | None
    taint_refused: bool
    read_tools: int  # ferramentas só de leitura declaradas e chamadas
    domains: list[Domain]
    empties: int
    coverage: bool
    offered: bool
    retries: int
    feedback: str | None
    pre_guard: str | None
    fail_domain: Domain | None
    answer: str
    guard: GuardOutcome
    status: TurnStatus
    abort: Literal["budget_exceeded", "llm_unavailable"] | None
    executed: int  # chamadas cujo handler rodou
    rest_empty: bool
    t_llm: float
    t_tools: float
    t_guard: float


def _env(config: Mapping[str, Any]) -> _Env:
    env = config["configurable"]["env"]
    assert isinstance(env, _Env)
    return env


async def _llm_node(state: _S, config: RunnableConfig) -> _S:
    env = _env(config)
    left = env.deadline - monotonic()
    if left <= 0:
        return {"abort": "budget_exceeded"}
    msgs = list(state["msgs"])
    if state["draft"] is not None:
        msgs.append(Message("user", REST_INSTRUCTION))
    feedback = state.get("feedback")
    if feedback:
        msgs += [Message("assistant", state.get("text") or ""), Message("user", feedback)]
    offer = state["rnd"] < env.max_rounds and not feedback
    specs = tuple(t.spec for t in env.tools.values()) if offer else ()
    t0 = monotonic()
    try:
        resp = await asyncio.wait_for(
            env.llm.generate(model=env.model, system=env.system, messages=msgs, tools=specs,
                             deadline=env.deadline),
            left,
        )  # fmt: skip
    except TimeoutError:
        return {"abort": "budget_exceeded"}
    except LLMUnavailable as e:
        return {"abort": "budget_exceeded" if e.reason == "timeout" else "llm_unavailable"}
    u, calls = state["usage"], resp.tool_calls if offer else ()
    out: _S = {
        "text": resp.text, "calls": calls, "meta": resp.provider_meta, "model": resp.model,
        "llm_calls": state["llm_calls"] + 1, "feedback": None, "abort": None,
        "t_llm": state["t_llm"] + (monotonic() - t0) * 1000,
        "usage": Usage(u.input_tokens + resp.usage.input_tokens,
                       u.output_tokens + resp.usage.output_tokens,
                       u.thinking_tokens + resp.usage.thinking_tokens,
                       u.cached_tokens + resp.usage.cached_tokens),
    }  # fmt: skip
    if calls:
        asst = Message("assistant", resp.text, calls, provider_meta=resp.provider_meta,
                       model=resp.model, turn=env.turn)  # fmt: skip
        out["msgs"], out["new"] = [*state["msgs"], asst], [*state["new"], asst]
    return out


@dataclass(frozen=True)
class _Res:
    call: ToolCall
    result: dict[str, Any]
    output: ToolOutput | None
    refusal: str | None
    failed: bool = False  # o handler levantou: diferente de vazio


async def _exec(env: _Env, ctx: ToolContext, call: ToolCall) -> _Res:
    """Valida, confere `allowed` e só então chama o handler; nada além do código estável sai."""
    tool = env.tools[call.name]
    try:
        args = validate_args(tool.spec, call.args)
        if (why := tool.allowed(ctx)) is not None:
            return _Res(call, {"erro": why}, None, why)
        out = await tool.handler(ctx, args)
    except ToolError as e:
        return _Res(call, {"erro": e.code}, None, None)
    except Exception as e:  # noqa: BLE001 - o texto da exceção nunca vai ao LLM nem ao log
        log.warning("ferramenta %s falhou: %s", call.name, type(e).__name__)
        return _Res(call, {"erro": FERRAMENTA_INDISPONIVEL}, None, None, failed=True)
    return _Res(call, wrap_tool_result(out), out, None)


async def _run_round(env: _Env, state: _S) -> tuple[list[_Res], bool]:
    calls, tainted, draft = state["calls"], state["tainted"], state["draft"]
    declared = [c for c in calls if c.name in env.tools]
    ctx = env.ctx_factory(state["rnd"], tainted)
    res: dict[str, _Res] = {}
    reads = [c for c in declared if env.tools[c.name].read_only]
    for c, r in zip(reads, await asyncio.gather(*(_exec(env, ctx, c) for c in reads)), strict=True):
        res[c.id] = r
    if tainted := tainted or any(r.output and r.output.untrusted for r in res.values()):
        ctx = env.ctx_factory(state["rnd"], True)  # o taint desta rodada vale para as escritas
    for c in (c for c in declared if not env.tools[c.name].read_only):
        r = (_Res(c, {"erro": FERRAMENTA_INDISPONIVEL}, None, None) if draft is not None
             else await _exec(env, ctx, c))  # fmt: skip
        draft = draft or (r.output.draft if r.output else None)
        res[c.id] = r
    unknown = {c.id: _Res(c, {"erro": FERRAMENTA_INDISPONIVEL}, None, None) for c in calls
               if c.name not in env.tools}  # fmt: skip
    return [res.get(c.id) or unknown[c.id] for c in calls], tainted


async def _tools_node(state: _S, config: RunnableConfig) -> _S:
    env = _env(config)
    get_stream_writer()(dict(_PROGRESS))
    t0 = monotonic()
    try:
        results, tainted = await asyncio.wait_for(
            _run_round(env, state), max(env.deadline - t0, 0.001)
        )
    except TimeoutError:
        return {"abort": "budget_exceeded"}
    trace, records, domains = list(state["trace"]), list(state["records"]), list(state["domains"])
    tool_msgs: list[Message] = []
    draft, taint_refused, coverage = state["draft"], state["taint_refused"], state["coverage"]
    read_tools, empties, offered = state["read_tools"], state["empties"], state["offered"]
    executed = state["executed"]
    for r in results:
        out = r.output
        tool_msgs.append(
            Message("tool", tool_call_id=r.call.id, tool_name=r.call.name, tool_result=r.result,
                    untrusted=bool(out and out.untrusted), turn=env.turn)
        )  # fmt: skip
        if (tool := env.tools.get(r.call.name)) is None:
            continue
        executed += out is not None or r.failed
        n_rec = len(out.records) if out else -1 if r.failed else 0  # -1: falhou, não vazio
        trace.append(ToolCallTrace(r.call.name, {k: str(v) for k, v in r.call.args.items()}, n_rec))
        read_tools += tool.read_only
        taint_refused = taint_refused or r.refusal == TAINT_NO_DRAFT
        if out is None:
            continue
        records += out.records
        domains.append(tool.domain)
        draft = draft or out.draft
        dump = json.dumps(out.data, ensure_ascii=False)
        coverage = coverage or bool(_COVERAGE.search(dump))
        offered = offered or "outras_matriculas" in out.data
        empties += EMPTY in dump
    return {
        "msgs": [*state["msgs"], *tool_msgs], "new": [*state["new"], *tool_msgs],
        "rnd": state["rnd"] + 1, "trace": trace, "records": records, "domains": domains,
        "empties": empties, "tainted": tainted, "draft": draft, "taint_refused": taint_refused,
        "read_tools": read_tools, "executed": executed, "coverage": coverage, "offered": offered,
        "t_tools": state["t_tools"] + (monotonic() - t0) * 1000,
    }  # fmt: skip


def _merge(records: Sequence[EntityRecord]) -> dict[str, EntityRecord]:
    """Uma entrada por registro distinto; mesma chave com campos diferentes vira `chave#n`."""
    out: dict[str, EntityRecord] = {}
    for r in records:
        if r in out.values():
            continue
        n = sum(k == r.key or k.startswith(f"{r.key}#") for k in out)
        out[r.key if n == 0 else f"{r.key}#{n}"] = r
    return out


def _check(env: _Env, state: _S, answer: str) -> GuardResult:
    return check_evidence(
        answer, question=env.question, records_by_entity=_merge(state["records"]),
        card_facts=env.card.facts, enrollment_indices=tuple(e.index for e in env.scope.enrollments),
        tool_calls_this_turn=tuple(state["trace"]), system_notes=env.system_notes,
        canary=env.settings.prompt_canary, prompt_prefix=SYSTEM_PREFIX, ontology=env.ontology,
    )  # fmt: skip


def _server_text(state: _S) -> list[str]:
    parts: list[str] = []
    if (d := state["draft"]) is not None:
        label = f" para {d.enrollment_label}" if d.enrollment_label else ""
        parts.append(DRAFT_SHOWN_TEMPLATE.format(servico=d.service_name_pt, para_matricula=label))
    if state["taint_refused"]:
        parts.append(TAINT_NO_DRAFT)
    return parts


async def _guard_node(state: _S, config: RunnableConfig) -> _S:
    env = _env(config)
    t0 = monotonic()
    rest = strip_markdown_and_codes((state["text"] or "").strip(), env.ontology).strip()
    fixed = _server_text(state)
    guard: GuardOutcome = "n/a"
    status: TurnStatus = "ok"
    domain = state["fail_domain"]
    pre = state["pre_guard"]
    rest_empty = not rest and pre is None  # o LLM não tinha resto, antes de qualquer guard
    if not rest and fixed:
        pass
    elif not rest:
        guard, status = "fallback", "fallback"
    else:
        res = _check(env, state, rest)
        if res.ok:
            guard = "retry_pass" if state["retries"] else "pass"
        elif env.allow_retry and not state["retries"]:
            kinds = ", ".join(sorted(res.kinds()))
            return {
                "retries": 1, "pre_guard": rest, "fail_domain": res.domain, "text": rest,
                "feedback": "Sua resposta foi reprovada na conferência com os dados "
                f"({kinds}). Reescreva usando só valores, datas e fatos do cartão e das "
                "ferramentas, sem prometer nada que não esteja lá.",
                "t_guard": state["t_guard"] + (monotonic() - t0) * 1000,
            }  # fmt: skip
        else:
            pre, domain = pre or rest, res.domain
            if (kept := degrade(rest, res)) is not None:
                rest, guard, status = kept, "degraded", "degraded"
            else:
                rest, guard, status = "", "fallback", "fallback"
    if guard == "fallback":
        rest = template_fallback(domain, records=state["records"], card_facts=env.card.facts,
                                 channel=env.channel, tainted=state["tainted"])  # fmt: skip
    answer = " ".join([*fixed, rest]).strip()
    new = list(state["new"])
    if answer:
        new.append(Message("assistant", answer, provider_meta=state["meta"], model=state["model"],
                           turn=env.turn))  # fmt: skip
    return {
        "answer": answer, "guard": guard, "status": status, "pre_guard": pre, "new": new,
        "feedback": None, "rest_empty": rest_empty,
        "t_guard": state["t_guard"] + (monotonic() - t0) * 1000,
    }  # fmt: skip


def _after_llm(state: _S) -> str:
    if state.get("abort"):
        return END
    return "tools" if state["calls"] else "guard"


def _after_guard(state: _S) -> str:
    return "llm" if state.get("feedback") else END


@cache
def build_graph() -> CompiledStateGraph:  # type: ignore[type-arg]
    """Compilado uma vez; tudo do turno (llm, ferramentas, prompt, prazo) vai em `configurable`."""
    g = StateGraph(_S)
    g.add_node("llm", _llm_node)
    g.add_node("tools", _tools_node)
    g.add_node("guard", _guard_node)
    g.add_edge(START, "llm")
    g.add_conditional_edges("llm", _after_llm, ["tools", "guard", END])
    g.add_edge("tools", "llm")
    g.add_conditional_edges("guard", _after_guard, ["llm", END])
    return g.compile()


def _fix_meta(m: Message, model: str) -> Message:
    """A assinatura de raciocínio de outro modelo é descartada."""
    if not m.model or m.model == model:
        return m
    calls = tuple(replace(c, provider_meta=None) for c in m.tool_calls)
    return replace(m, provider_meta=None, tool_calls=calls)


def _leaves(v: Any) -> list[str]:
    if isinstance(v, Mapping):
        return [x for i in v.values() for x in _leaves(i)]
    if isinstance(v, list | tuple):
        return [x for i in v for x in _leaves(i)]
    return [str(v)]


def _compact(m: Message, final: str) -> Message:
    """Saída grande de turno antigo: só os campos que a resposta citou."""
    r = m.tool_result
    if m.role != "tool" or not r or len(json.dumps(r, ensure_ascii=False)) <= COMPACT_CHARS:
        return m
    keep = {k: v for k, v in r.items() if any(len(x) > 2 and x in final for x in _leaves(v))}
    return replace(m, tool_result={**keep, "_compactado": True})


def _history(conv: Conversation, model: str, turns: int) -> list[Message]:
    out: list[Message] = []
    for t in conv.turns[max(len(conv.turns) - turns, 0) :] if turns > 0 else ():
        final = next((m.text for m in reversed(t) if m.role == "assistant" and m.text), "")
        out += [_compact(_fix_meta(m, model), final) for m in t]
    return out


def _domain(state: _S, answer: str) -> Domain | Literal["cartao", "geral"]:
    if state["domains"]:
        return Counter(state["domains"]).most_common(1)[0][0]
    return "geral" if answer.startswith(REFUSAL_PREFIX) else "cartao"


def _kind(state: _S) -> AnswerKind:
    d, answer = state["draft"], state["answer"]
    if d is not None and d.key.service_code in TRANSBORDO:
        return "transbordo"
    if state["coverage"]:
        return "fora_cobertura"
    if state["read_tools"] and state["empties"] >= len(state["domains"]) > 0:
        return "vazio"
    return "recusa" if answer.startswith(REFUSAL_PREFIX) else "resposta"


def _bare(
    status: TurnStatus, answer: str, model: str, *, total_ms: float = 0.0, **extra: Any
) -> TurnResult:
    base: dict[str, Any] = {
        "answer": answer, "status": status, "guard": "n/a", "draft": None, "tool_calls": (),
        "tools_class": "card", "llm_calls": 0, "model": model, "answer_kind": "resposta",
        "domain": "geral", "draft_only": False, "usage": Usage(),
        "timings_ms": {"total": total_ms}, "first_event_ms": None, "pre_guard_answer": None,
        "new_messages": (), "records": (),
    }  # fmt: skip
    return TurnResult(**{**base, **extra})


async def run_turn(
    *, llm: LLMProvider, settings: AppSettings, scope: StudentScope, card: StudentCard,
    conversation: Conversation, text: str, channel: Literal["web", "a2a"],
    ctx_factory: Callable[[int, bool], ToolContext], deadline: float,
    writer: Callable[[Mapping[str, Any]], None], model: str,
    spend_level: SpendLevel = "normal", ontology: Ontology | None = None,
    tools: Sequence[Tool] | None = None,
) -> TurnResult:  # fmt: skip
    """Um turno, sem E/S de histórico: T25 grava `new_messages` e as flags do resultado."""
    t0, lim = monotonic(), settings.limits
    capped = lim.degrade_at is not None  # perfil eval nunca degrada por custo
    if capped and spend_level == "blocked":
        return _bare("blocked_limit", BLOCKED_TEXT, model)
    degraded = capped and spend_level == "degraded"
    rounds = lim.degrade_rounds if degraded else lim.max_rounds
    tl = tuple(tools if tools is not None else build_tools())
    turn = 1 + max((m.turn for t in conversation.turns for m in t), default=-1)
    user = Message("user", text, turn=turn)
    env = _Env(
        llm, settings, {t.spec.name: t for t in tl}, system_prompt(settings),
        text, scope, card, channel, ctx_factory, deadline, model, turn, rounds, not degraded,
        ontology or default_ontology(),
        tuple(m.text for t in conversation.turns for m in t if m.role == "system_note" and m.text),
    )  # fmt: skip
    init: _S = {
        "msgs": [*card_messages(card), *_history(conversation, model, lim.history_turns), user],
        "new": [user], "rnd": 0, "llm_calls": 0, "usage": Usage(), "text": None, "calls": (),
        "meta": None, "model": model, "trace": [], "records": [],
        "tainted": conversation.tainted, "draft": None, "taint_refused": False,
        "read_tools": 0, "domains": [], "empties": 0, "coverage": False, "offered": False,
        "executed": 0, "rest_empty": False, "retries": 0, "feedback": None, "pre_guard": None,
        "fail_domain": None, "answer": "",
        "guard": "n/a", "status": "ok", "abort": None, "t_llm": 0.0, "t_tools": 0.0,
        "t_guard": 0.0,
    }  # fmt: skip
    first: float | None = None
    final: _S = init
    graph = build_graph()
    cfg: Any = {"recursion_limit": 2 * rounds + 6, "configurable": {"env": env}}
    stream: Any = graph.astream(init, cfg, stream_mode=["custom", "values"])
    async for mode, chunk in stream:
        if mode == "custom":
            first = first if first is not None else (monotonic() - t0) * 1000
            writer(chunk)
        else:
            final = chunk
    total = (monotonic() - t0) * 1000
    tainted = final["tainted"] and not conversation.tainted
    if abort := final.get("abort"):
        fixed = " ".join(_server_text(final))
        msg = BUDGET_EXCEEDED_TEXT if abort == "budget_exceeded" else LLM_UNAVAILABLE_TEXT
        return _bare(
            abort, fixed or msg, model, total_ms=total, draft=final["draft"],
            llm_calls=final["llm_calls"], usage=final["usage"], became_tainted=tainted,
        )  # fmt: skip
    n = final["executed"]
    d, answer = final["draft"], final["answer"]
    return TurnResult(
        answer=answer, status=final["status"], guard=final["guard"], draft=d,
        tool_calls=tuple(final["trace"]), tools_class="card" if n == 0 else "1" if n == 1 else "2+",
        llm_calls=final["llm_calls"], model=final["model"], answer_kind=_kind(final),
        domain=_domain(final, answer),
        draft_only=d is not None and not final["read_tools"] and final["rest_empty"],
        usage=final["usage"],
        timings_ms={"total": total, "llm": final["t_llm"], "tools": final["t_tools"],
                    "guard": final["t_guard"]},
        first_event_ms=total if n == 0 else first, pre_guard_answer=final["pre_guard"],
        new_messages=tuple(final["new"]), records=tuple(final["records"]),
        became_tainted=tainted, offered_other_enrollment=final["offered"], cost_degraded=degraded,
    )  # fmt: skip
