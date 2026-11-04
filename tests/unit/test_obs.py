import asyncio
import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest
import structlog
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from prometheus_client import REGISTRY

from app.agent.graph import AnswerKind, TurnStatus, _exec, bare_result
from app.agent.llm import ToolCall
from app.config import AppSettings
from app.obs import annotations
from app.obs.logging import configure_logging
from app.obs.metrics import render
from app.obs.tracing import session_id, setup_tracing, span
from app.service.turn import DefaultTurnService
from app.tools.base import Domain, Tool, ToolContext, ToolOutput, ToolSpec, schema
from tests.unit.turn_env import MODEL, OK, SCOPE, make_service, patch_db, scripted

pytestmark = pytest.mark.usefixtures("app_secrets")
SECRET = "MEU-TEXTO-SECRETO-do-aluno"
CPF = "123.456.789-09"


@pytest.fixture(autouse=True)
def _db(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_db(monkeypatch)


@pytest.fixture
def exp() -> Iterator[InMemorySpanExporter]:
    e = InMemorySpanExporter()
    setup_tracing(AppSettings(), exporter=e)
    yield e
    setup_tracing(AppSettings(), exporter=InMemorySpanExporter())  # solta o exportador do teste


async def run(svc: Any, text: str = SECRET, cid: str = "conv-1") -> None:
    async def emit(e: Mapping[str, Any]) -> None:
        pass

    adm = await svc.admit(scope=SCOPE, channel="web")
    r = await svc.handle(admission=adm, scope=SCOPE, conversation_id=cid, text=text, channel="web",
                         emit=emit)  # fmt: skip
    await svc.settle(adm, r)


def dump(exp: InMemorySpanExporter) -> str:
    out = []
    for s in exp.get_finished_spans():
        out += [dict(s.attributes or {}), [(e.name, dict(e.attributes or {})) for e in s.events]]
    return json.dumps(out, default=str, ensure_ascii=False)


async def test_spans_no_content_by_default(exp: InMemorySpanExporter) -> None:
    await run(make_service(AppSettings(), scripted(OK)))
    d = dump(exp)
    assert exp.get_finished_spans() and SECRET not in d and OK not in d


async def test_capture_content_only_dev_eval(
    exp: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError):
        AppSettings(otel_capture_content=True)  # dev + default: recusado
    s = AppSettings(otel_capture_content=True, limits_profile="eval")
    setup_tracing(s, exporter=exp)
    await run(make_service(s, scripted(OK)))
    d = dump(exp)
    assert SECRET in d and OK in d


def test_exception_type_only(exp: InMemorySpanExporter) -> None:
    with pytest.raises(RuntimeError), span("x", "TOOL"):
        raise RuntimeError(f"detalhe com {CPF}")
    (s,) = exp.get_finished_spans()
    d = dump(exp)
    assert CPF not in d and "detalhe" not in d
    assert s.attributes and s.attributes["exception.type"] == "RuntimeError"
    assert not any(e.name == "exception" for e in s.events)


async def test_span_kinds_and_token_details(exp: InMemorySpanExporter) -> None:
    await run(make_service(AppSettings(), scripted(OK)))
    by = {s.name: s for s in exp.get_finished_spans()}
    kinds = {n: (s.attributes or {})["openinference.span.kind"] for n, s in by.items()}
    assert kinds["chat.turn"] == "AGENT" and kinds["agent.call_model"] == "LLM"
    assert kinds["agent.guard"] == "GUARDRAIL"
    llm = by["agent.call_model"].attributes or {}
    assert llm["llm.model_name"] == MODEL and llm["gen_ai.request.model"] == MODEL
    assert llm["llm.token_count.prompt"] == 100 and llm["llm.token_count.completion"] == 10
    assert "llm.token_count.prompt_details.cache_read" in llm
    assert "llm.token_count.completion_details.reasoning" in llm
    root = by["chat.turn"].attributes or {}
    assert root["llm.calls"] == 1 and root["prompt.sha"] and root["tools.sha"]
    assert (by["agent.guard"].attributes or {})["guard.outcome"] == "pass"
    # filhos do AGENT, no mesmo trace
    assert {s.context.trace_id for s in by.values()} == {by["chat.turn"].context.trace_id}


async def test_tool_args_enumerated_no_pii(exp: InMemorySpanExporter) -> None:
    async def ok(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        return ToolOutput({}, ())

    async def boom(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        raise RuntimeError(CPF)

    props = {"status": {"type": "string", "enum": ["aberta", "paga"]},
             "texto_livre": {"type": "string"}}  # fmt: skip
    spec = ToolSpec("get_x", "d", schema(props))
    dom: Domain = "financeiro"
    env = SimpleNamespace(tools={"get_x": Tool(spec, ok, dom), "bad": Tool(spec, boom, dom)})
    call = ToolCall("1", "get_x", {"status": "aberta", "texto_livre": f"meu cpf {CPF}"})
    await _exec(env, None, call)  # type: ignore[arg-type]
    await _exec(env, None, ToolCall("2", "bad", call.args))  # type: ignore[arg-type]
    await _exec(env, None, ToolCall("3", "get_x", {"status": f"meu cpf {CPF} " + "x" * 5000}))  # type: ignore[arg-type]
    d = dump(exp)
    assert CPF not in d and "meu cpf" not in d and "xxxx" not in d
    ok_s, bad_s, inv_s = [s.attributes or {} for s in exp.get_finished_spans()]
    assert inv_s["tool.args.status"] == "invalido" and "tool.failed" not in inv_s
    assert ok_s["tool.name"] == "get_x" and ok_s["tool.args.status"] == "aberta"
    assert ok_s["tool.args.count"] == 2 and ok_s["tool.returned_records"] == 0
    assert (
        bad_s["tool.failed"] is True
        and "tool.refused" not in bad_s
        and "tool.returned_records" not in bad_s
    )  # nunca -1


def test_session_id_hmac() -> None:
    s = AppSettings()
    a = session_id(s, "conv-1")
    assert a == session_id(s, "conv-1") and a != session_id(s, "conv-2") and "conv-1" not in a
    assert len(a) == 32 and a != session_id(AppSettings(hmac_key="k" * 40), "conv-1")


# --- métricas ---------------------------------------------------------------------------------
def _sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_metrics_multiprocess_and_buckets(tmp_path: Path) -> None:
    code = (
        "from app.obs.metrics import METRICS as M;"
        "M['agent_turns_total'].labels(status='ok',channel='web').inc();"
        "M['agent_turn_seconds'].labels(tools_class='card',llm_calls='1',guard='pass',"
        "model='m').observe(3.5)"
    )
    for _ in range(2):  # dois processos escrevem; a leitura soma
        subprocess.run([sys.executable, "-c", code], check=True,
                       env={"PROMETHEUS_MULTIPROC_DIR": str(tmp_path), "PATH": ""})  # fmt: skip
    text = render(str(tmp_path)).decode()
    assert 'agent_turns_total{channel="web",status="ok"} 2.0' in text
    les = [ln.split('le="')[1].split('"')[0] for ln in text.splitlines()
           if ln.startswith("agent_turn_seconds_bucket")]  # fmt: skip
    assert les == ["0.5", "1.0", "2.0", "3.0", "4.0", "6.0", "8.0", "12.0", "16.0", "24.0", "+Inf"]


async def test_turns_total_status_values() -> None:
    statuses = set(get_args(TurnStatus))
    assert statuses == {"ok", "degraded", "fallback", "fixed", "blocked_limit", "shed",
                        "budget_exceeded", "llm_unavailable", "cancelled"}  # fmt: skip
    svc = make_service(AppSettings(), scripted(OK))
    for st in statuses:
        before = _sample("agent_turns_total", status=st, channel="web")

        async def emit(e: Mapping[str, Any]) -> None:
            pass

        await DefaultTurnService._done(svc, bare_result(st, "x", MODEL), emit, "t", "web")  # type: ignore[arg-type]
        if st != "cancelled":  # `cancelled` é contado no settle
            assert _sample("agent_turns_total", status=st, channel="web") == before + 1
    adm = await svc.admit(scope=SCOPE, channel="web")
    before = _sample("agent_turns_total", status="cancelled", channel="web")
    await svc.settle(adm, None, cancelled=True)
    assert _sample("agent_turns_total", status="cancelled", channel="web") == before + 1


async def test_first_event_by_class() -> None:
    k = {"tools_class": "card"}
    before = _sample("agent_first_event_seconds_count", **k)
    await run(make_service(AppSettings(), scripted(OK)))
    assert _sample("agent_first_event_seconds_count", **k) == before + 1


def test_answer_kind_metric_labels_bounded() -> None:
    from app.tools.base import Domain

    assert len(get_args(AnswerKind)) == 5 and len(get_args(Domain)) + 2 == 7  # + cartao, geral


# --- anotações --------------------------------------------------------------------------------
async def test_annotation_after_flush_with_retry() -> None:
    order: list[str] = []

    def send(batch: list[annotations.Annotation]) -> None:
        order.append(f"send:{batch[0].name}")
        if order.count("flush") and len([o for o in order if o.startswith("send")]) == 1:
            raise ConnectionError("1ª falha")

    annotations.configure(flush=lambda: order.append("flush"), send=send, backoff_s=0.01)
    annotations.annotate("t" * 32, "thumb", "down", 0.0, None)
    await annotations.drain()
    assert order == ["flush", "send:thumb", "send:thumb"]


async def test_force_flush_in_thread_with_timeout() -> None:
    release, sent, ticks = threading.Event(), [], []
    annotations.configure(flush=lambda: release.wait(5), send=sent.append, backoff_s=0.01)
    annotations.annotate("t" * 32, "thumb", "up", 1.0, None)

    async def ticker() -> None:
        while True:
            ticks.append(1)
            await asyncio.sleep(0.05)

    task = asyncio.create_task(ticker())
    t0 = time.monotonic()
    await annotations.drain()
    elapsed = time.monotonic() - t0
    task.cancel()
    release.set()
    assert elapsed <= 2.2 and len(ticks) >= 10 and len(sent) == 1


# --- logs -------------------------------------------------------------------------------------
@pytest.fixture
def restore_logging() -> Iterator[None]:
    import logging

    root = logging.getLogger()
    handlers, level, ua = root.handlers[:], root.level, logging.getLogger("uvicorn.access").disabled
    yield
    root.handlers[:], root.level = handlers, level
    logging.getLogger("uvicorn.access").disabled = ua
    structlog.reset_defaults()


@pytest.mark.usefixtures("restore_logging")
def test_logs_no_pii(capsys: pytest.CaptureFixture[str], exp: InMemorySpanExporter) -> None:
    import logging

    configure_logging(AppSettings())
    with span("chat.turn", "AGENT") as sp:
        structlog.get_logger().info("evento", cpf=CPF, nome="Maria Silva", texto_livre=SECRET,
                                    validation_code="ABC123", ok=1)  # fmt: skip
        logging.getLogger("x").warning("falhou para %s", CPF)
        try:
            raise RuntimeError(f"vazou {CPF}")
        except RuntimeError:
            logging.getLogger("x").exception("erro")
        tid = sp.trace_id
    out = capsys.readouterr().out
    lines = [json.loads(ln) for ln in out.splitlines() if ln.startswith("{")]
    assert len(lines) == 3 and all(ln["trace_id"] == tid for ln in lines)
    for bad in (CPF, "Maria", SECRET, "ABC123", "vazou"):
        assert bad not in out
    assert lines[0]["ok"] == 1 and lines[2]["exception_type"] == "RuntimeError"


@pytest.mark.usefixtures("restore_logging")
def test_uvicorn_access_silent_and_structlog_exception(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import logging

    configure_logging(AppSettings())
    logging.getLogger("uvicorn.access").info("GET /auth?ticket=SEGREDO HTTP/1.1")
    try:
        raise ValueError("vazou")
    except ValueError:
        structlog.get_logger().exception("erro")
    out = capsys.readouterr().out
    assert "SEGREDO" not in out and "vazou" not in out
    assert json.loads(out.splitlines()[0])["exception_type"] == "ValueError"


@pytest.fixture(autouse=True)
def _fresh_annotations() -> Iterator[None]:
    annotations._queue.clear()
    annotations._worker = None
    annotations._lock = None
    yield
    annotations._queue.clear()
    annotations._worker = None
    annotations._lock = None


async def test_annotation_during_slow_drain_not_stranded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(annotations, "BATCH_WINDOW_S", 0)
    sent: list[str] = []
    gate = threading.Event()
    annotations.configure(
        flush=lambda: gate.wait(2), send=lambda b: sent.extend(a.name for a in b), backoff_s=0.01
    )
    annotations.annotate("t" * 32, "a", "x", None, None)
    await asyncio.sleep(0.1)  # o worker está dentro do drain, preso no flush
    annotations.annotate("t" * 32, "b", "x", None, None)  # chega durante o drain
    gate.set()
    assert annotations._worker is not None
    await annotations._worker
    assert sent == ["a", "b"] and not annotations._queue


async def test_concurrent_drains_send_each_once() -> None:
    sent: list[str] = []
    annotations.configure(
        flush=None,
        send=lambda b: sent.extend(a.name for a in b),
        backoff_s=0.01,  # type: ignore[arg-type]
    )
    annotations.annotate("t" * 32, "a", "x", None, None)
    annotations.annotate("t" * 32, "b", "x", None, None)
    await asyncio.gather(annotations.drain(), annotations.drain())
    assert sorted(sent) == ["a", "b"] and not annotations._queue


async def test_failed_send_keeps_queue_and_annotate_span() -> None:
    sent: list[annotations.Annotation] = []
    ok = {"v": False}

    def send(b: list[annotations.Annotation]) -> None:
        if not ok["v"]:
            raise ConnectionError("fora")
        sent.extend(b)

    annotations.configure(flush=None, send=send, backoff_s=0.001)  # type: ignore[arg-type]
    annotations._queue.clear()
    annotations.annotate_span("ab" * 8, "thumb", "down", 0.0, None)
    assert await annotations.drain() is False and len(annotations._queue) == 1
    ok["v"] = True
    assert await annotations.drain() is True and not annotations._queue
    assert sent[0].span_id == "ab" * 8 and len(sent) == 1  # sem duplicar


def test_roots_eviction() -> None:
    annotations._roots.clear()
    for i in range(10_005):
        annotations.remember_root(f"t{i}", "s")
    assert len(annotations._roots) == 10_000 and "t0" not in annotations._roots
    assert "t10004" in annotations._roots


async def test_meta_span_id_only_dev_eval(exp: InMemorySpanExporter) -> None:
    async def metas(s: AppSettings) -> dict[str, Any]:
        got: list[Mapping[str, Any]] = []

        async def emit(e: Mapping[str, Any]) -> None:
            got.append(e)

        svc = make_service(s, scripted(OK))
        adm = await svc.admit(scope=SCOPE, channel="web")
        await svc.handle(admission=adm, scope=SCOPE, conversation_id="c", text="oi",
                         channel="web", emit=emit)  # fmt: skip
        await svc.settle(adm, None)
        return dict(got[0])

    assert "span_id" not in await metas(AppSettings())
    m = await metas(AppSettings(limits_profile="eval"))
    root = next(s for s in exp.get_finished_spans() if s.name == "chat.turn" and s.context.span_id
                == int(m["span_id"], 16))  # fmt: skip
    assert format(root.context.trace_id, "032x") == m["trace_id"]


async def test_db_query_span(exp: InMemorySpanExporter) -> None:
    from contextlib import asynccontextmanager
    from uuid import UUID

    from app.data.db import with_scope

    class Conn:
        async def execute(self, *a: Any) -> None: ...

        @asynccontextmanager
        async def transaction(self) -> Any:
            yield

    class Pool:
        @asynccontextmanager
        async def connection(self) -> Any:
            yield Conn()

    async with with_scope(Pool(), UUID(int=1), ("UniSaoPaulo",)):  # type: ignore[arg-type]
        pass
    (s,) = exp.get_finished_spans()
    assert s.name == "db.query" and str(UUID(int=1)) not in dump(exp)
