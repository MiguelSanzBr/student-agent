"""Spikes ao vivo da Task 3. Rodar: pytest -m live tests/live/test_spikes.py (Gemini pula sem GEMINI_API_KEY)."""

import asyncio
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
pytestmark = pytest.mark.live

# ------------------------------------------------------------------ A2A 1.0


def _a2a():
    import a2a_harness as H
    from a2a.helpers.proto_helpers import new_text_message
    from a2a.types import a2a_pb2 as P

    def U(text, **kw):
        return P.SendMessageRequest(message=new_text_message(text, role=P.Role.ROLE_USER, **kw))

    async def send(c, req):
        async for ev in c.send_message(req):
            return ev

    async def raw(h, method, params):
        r = await h.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"A2A-Version": "1.0"},
        )
        return r.json()

    return H, P, U, send, raw


def test_a2a_owner_isolation_and_codes():
    from a2a.utils.errors import (
        TaskNotCancelableError,
        TaskNotFoundError,
        UnsupportedOperationError,
    )

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "e2e"))
    import a2a_codes as C

    H, P, U, send, raw = _a2a()

    async def go():
        app = H.make_app()
        ca, ha = await H.client_for(app, "alice")
        cb, hb = await H.client_for(app, "bob")
        t = (await send(ca, U("oi"))).task
        assert t.status.state == P.TaskState.TASK_STATE_COMPLETED
        assert (await ca.get_task(P.GetTaskRequest(id=t.id))).id == t.id  # controle positivo
        with pytest.raises(TaskNotFoundError):
            await cb.get_task(P.GetTaskRequest(id=t.id))
        with pytest.raises(TaskNotFoundError):  # SendMessage com taskId alheio
            await send(cb, U("x", task_id=t.id, context_id=t.context_id))
        with pytest.raises(UnsupportedOperationError):  # dono, tarefa já terminal
            await send(ca, U("x", task_id=t.id, context_id=t.context_id))
        # contextId alheio: aceito, vira tarefa NOVA do chamador (o isolamento é do executor)
        tb = (await send(cb, U("ctx", context_id=t.context_id))).task
        assert tb.id != t.id and tb.context_id == t.context_id
        la = await ca.list_tasks(P.ListTasksRequest())
        lb = await cb.list_tasks(P.ListTasksRequest())
        assert [x.id for x in la.tasks] == [t.id] and [x.id for x in lb.tasks] == [tb.id]
        lx = await (await H.client_for(app, "carol"))[0].list_tasks(
            P.ListTasksRequest(context_id=t.context_id)
        )
        assert len(lx.tasks) == 0  # alheio -> lista vazia
        with pytest.raises(TaskNotCancelableError):
            await ca.cancel_task(P.CancelTaskRequest(id=t.id))
        with pytest.raises(TaskNotFoundError):
            await cb.cancel_task(P.CancelTaskRequest(id=t.id))
        codes = {}
        for m, p in [
            ("GetTask", {"id": "x"}),
            ("CancelTask", {"id": t.id}),
            ("CreateTaskPushNotificationConfig", {"taskId": t.id, "url": "http://x"}),
            ("GetTaskPushNotificationConfig", {"taskId": t.id, "id": "a"}),
            ("ListTaskPushNotificationConfigs", {"taskId": t.id}),
            ("DeleteTaskPushNotificationConfig", {"taskId": t.id, "id": "a"}),
            ("GetExtendedAgentCard", {}),
            ("SubscribeToTask", {"id": t.id}),
            (
                "SendStreamingMessage",
                {"message": {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "x"}]}},
            ),
            ("Inexistente", {}),
        ]:
            codes[m] = (await raw(ha, m, p))["error"]["code"]
        return codes

    codes = asyncio.run(go())
    assert codes["GetTask"] == C.TASK_NOT_FOUND
    assert codes["CancelTask"] == C.TASK_NOT_CANCELABLE
    assert {
        codes[k]
        for k in (
            "CreateTaskPushNotificationConfig",
            "GetTaskPushNotificationConfig",
            "ListTaskPushNotificationConfigs",
            "DeleteTaskPushNotificationConfig",
        )
    } == {C.PUSH_UNSUPPORTED}
    assert codes["SubscribeToTask"] == codes["SendStreamingMessage"] == C.UNSUPPORTED
    assert codes["GetExtendedAgentCard"] == C.EXTENDED_CARD_UNSUPPORTED  # extended_agent_card=False
    assert codes["Inexistente"] == C.METHOD_NOT_FOUND


def test_a2a_extended_card_not_configured_code():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "e2e"))
    import a2a_codes as C
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore
    from starlette.applications import Starlette

    H, P, U, send, raw = _a2a()
    c = H.card()
    c.capabilities.extended_agent_card = True
    app = Starlette(
        routes=create_jsonrpc_routes(
            DefaultRequestHandler(H.Exec(), InMemoryTaskStore(), c), "/a2a", H.Builder()
        )
    )
    r = asyncio.run(raw(H.http(app, "alice"), "GetExtendedAgentCard", {}))
    assert r["error"]["code"] == C.EXTENDED_CARD_NOT_CONFIGURED


def test_a2a_rejected_task_carries_retry_after():
    H, P, U, send, raw = _a2a()

    async def go():
        c, _ = await H.client_for(H.make_app(), "alice")
        return (await send(c, U("reject"))).task

    t = asyncio.run(go())
    assert t.status.state == P.TaskState.TASK_STATE_REJECTED
    assert t.metadata["retry_after_s"] == 7  # número vira float (Struct)
    assert "Tente de novo em 7 s" in t.status.message.parts[0].text


def test_a2a_exceptions_leak_message_and_log(caplog):
    """Exceção no executor/store/builder: o SDK devolve InternalError(str(e)) e loga -> wrappers são obrigatórios."""
    from a2a.utils.errors import InternalError

    H, P, U, send, raw = _a2a()
    caplog.set_level(logging.DEBUG)

    async def go():
        app = H.make_app()
        c, h = await H.client_for(app, "alice")
        with pytest.raises(InternalError) as e:
            await send(c, U("boom"))
        assert "segredo-do-executor" in e.value.message
        H.Builder.boom = True
        try:
            r1 = await raw(h, "GetTask", {"id": "x"})
        finally:
            H.Builder.boom = False
        app2 = H.make_app(H.BoomStore())
        _, h2 = await H.client_for(app2, "alice")
        H.BoomStore.armed = True
        try:
            r2 = await raw(h2, "GetTask", {"id": "x"})
        finally:
            H.BoomStore.armed = False
        return r1, r2

    r1, r2 = asyncio.run(go())
    assert (r1["error"]["code"], r1["error"]["message"]) == (-32603, "segredo-do-builder")
    assert (r2["error"]["code"], r2["error"]["message"]) == (-32603, "segredo-do-store")
    texts = [
        (r.name, r.levelname, r.exc_info is not None)
        for r in caplog.records
        if "segredo" in r.getMessage() or (r.exc_info and "segredo" in str(r.exc_info[1]))
    ]
    assert any(n.startswith("a2a") for n, _, _ in texts), (
        texts
    )  # loggers do SDK registram o segredo
    # Silenciar o logger "a2a" (e filhos) esconde tudo isso?
    caplog.clear()
    logging.getLogger("a2a").setLevel(logging.CRITICAL + 1)
    try:
        asyncio.run(go())
    finally:
        logging.getLogger("a2a").setLevel(logging.NOTSET)
    assert not [r for r in caplog.records if r.name.startswith("a2a")]


# ------------------------------------------------------------------ LangGraph


def test_langgraph_two_nodes_writer_cancel_mermaid():
    from typing import TypedDict

    from langgraph.config import get_stream_writer
    from langgraph.graph import END, START, StateGraph

    class S(TypedDict):
        x: int

    async def a(s: S) -> S:
        get_stream_writer()({"fase": "a"})
        return {"x": s["x"] + 1}

    async def b(s: S) -> S:
        get_stream_writer()({"fase": "b"})
        await asyncio.sleep(10)  # nó longo para o teste de cancelamento
        return {"x": s["x"] * 2}

    g = StateGraph(S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    app = g.compile()
    mer = app.get_graph().draw_mermaid()
    assert "a --> b" in mer and "__start__" in mer

    async def go():
        got = []

        async def consume():
            async for ev in app.astream({"x": 1}, stream_mode="custom"):
                got.append(ev)

        t = asyncio.create_task(consume())
        await asyncio.sleep(0.3)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        return got

    assert asyncio.run(go()) == [{"fase": "a"}, {"fase": "b"}]


# ------------------------------------------------------------------ Phoenix (imagem em container sa-spike-*)

PHOENIX_URL = os.environ.get("SPIKE_PHOENIX_URL")


@pytest.mark.skipif(not PHOENIX_URL, reason="defina SPIKE_PHOENIX_URL (container sa-spike-phoenix)")
def test_phoenix_span_flush_annotation():
    import time

    import httpx
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from phoenix.client import Client

    tp = TracerProvider()
    tp.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{PHOENIX_URL}/v1/traces"), schedule_delay_millis=60_000
        )
    )
    tracer = tp.get_tracer("spike")
    with tracer.start_as_current_span("spike-root") as sp:
        sp.set_attribute("openinference.span.kind", "CHAIN")
        span_id = format(sp.get_span_context().span_id, "016x")
    t0 = time.monotonic()
    ok = asyncio.run(asyncio.wait_for(asyncio.to_thread(tp.force_flush, 2000), timeout=2.5))
    assert ok is True and time.monotonic() - t0 < 2.5
    c = Client(base_url=PHOENIX_URL)
    for _ in range(20):
        spans = c.spans.get_spans(project_identifier="default", limit=50)
        if any(s["context"]["span_id"] == span_id for s in spans):
            break
        time.sleep(0.5)
    else:
        pytest.fail("span não apareceu")
    c.spans.add_span_annotation(
        span_id=span_id,
        annotation_name="thumb",
        annotator_kind="HUMAN",
        label="down",
        score=0.0,
        sync=True,
    )
    r = httpx.get(
        f"{PHOENIX_URL}/v1/projects/default/span_annotations", params={"span_ids": span_id}
    )
    assert r.status_code == 200 and r.json()["data"][0]["name"] == "thumb"
    # persistência: restart do container e o span + anotação continuam lá
    box = os.environ.get("SPIKE_PHOENIX_CONTAINER")
    if box:
        import subprocess

        subprocess.run(["docker", "restart", box], check=True, capture_output=True)
        for _ in range(60):
            try:
                if httpx.get(f"{PHOENIX_URL}/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        spans = Client(base_url=PHOENIX_URL).spans.get_spans(project_identifier="default", limit=50)
        assert any(s["context"]["span_id"] == span_id for s in spans)
        r = httpx.get(
            f"{PHOENIX_URL}/v1/projects/default/span_annotations", params={"span_ids": span_id}
        )
        assert r.json()["data"][0]["name"] == "thumb"


# ------------------------------------------------------------------ Gemini (pula sem GEMINI_API_KEY; NÃO executado na Task 3)

KEY = os.environ.get("GEMINI_API_KEY")
needs_key = pytest.mark.skipif(not KEY, reason="pendente: sem GEMINI_API_KEY")
MODELS = ("gemini-3.5-flash-lite", "gemini-3.8-flash")
TOOL = {
    "name": "get_student_card",
    "description": "Ficha do aluno.",
    "parameters_json_schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"enrollment": {"type": "string", "enum": ["1", "2", "3"]}},
        "required": ["enrollment"],
    },
}


def _gen():
    from google import genai

    return genai.Client(api_key=KEY)


@needs_key
def test_gemini_models_listed():
    names = {m.name.removeprefix("models/") for m in _gen().models.list()}
    missing = [m for m in MODELS if m not in names]
    assert not missing, (
        f"IDs ausentes: {missing}; disponíveis: {sorted(n for n in names if 'gemini' in n)}"
    )


@needs_key
@pytest.mark.parametrize("model", MODELS)
def test_gemini_latency_gate(model):
    """20 chamadas que emitem ferramenta + 20 que respondem (MINIMAL, ~1.500 tokens, 11 declarações)."""
    import json
    import time

    from google.genai import types as T

    c = _gen()
    decls = [{**TOOL, "name": f"{TOOL['name']}_{i}" if i else TOOL["name"]} for i in range(11)]
    cfg = T.GenerateContentConfig(
        system_instruction="Você é o atendimento ao aluno. " * 150,
        tools=[T.Tool(function_declarations=decls)],
        thinking_config=T.ThinkingConfig(thinking_level="MINIMAL"),
    )
    emit, answer = [], []
    for _ in range(20):
        t0 = time.monotonic()
        r = c.models.generate_content(
            model=model, contents="Qual a minha ficha? matrícula 1", config=cfg
        )
        emit.append(time.monotonic() - t0)
        part = r.candidates[0].content.parts[0]
        assert part.function_call is not None
        hist = [
            T.Content(role="user", parts=[T.Part(text="Qual a minha ficha? matrícula 1")]),
            r.candidates[0].content,
            T.Content(
                role="user",
                parts=[
                    T.Part.from_function_response(
                        name=part.function_call.name, response={"nome": "Aluno Teste"}
                    )
                ],
            ),
        ]
        t0 = time.monotonic()
        c.models.generate_content(model=model, contents=hist, config=cfg)
        answer.append(time.monotonic() - t0)
    with open(f"docs/evidence/gemini_samples_{model}.json", "w") as f:
        json.dump({"emit_s": emit, "answer_s": answer}, f)
