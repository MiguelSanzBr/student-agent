import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
import pytest

from evals.redteam.api_attacks import KEYWORD, run_all

SECRET = "s" * 40
VICTIM, ATTACKER = uuid4(), uuid4()


def _sub(token: str | None, aud: str) -> UUID | None:
    try:
        return UUID(jwt.decode(token or "", SECRET, algorithms=["HS256"], audience=aud)["sub"])
    except Exception:
        return None


def _sse(answer: str, trace: str = "tr") -> str:
    return (
        f'event: meta\ndata: {{"trace_id": "{trace}"}}\n\n'
        f"event: answer\ndata: {json.dumps({'text': answer})}\n\n"
        'event: done\ndata: {"status": "ok"}\n\n'
    )


class Server:
    """Servidor falso. `leaky` desliga a posse e o histórico por dono/canal; `amnesic` faz o
    dono esquecer a palavra (controle positivo deve acusar)."""

    def __init__(self, leaky: bool = False, amnesic: bool = False) -> None:
        self.leaky, self.amnesic = leaky, amnesic
        self.tasks: dict[str, UUID] = {}
        self.hist: dict[tuple[Any, ...], bool] = {}
        self.traces: dict[str, UUID] = {}
        self.drafts: dict[str, UUID] = {}
        self.draft_seed = lambda sub, conv: self._seed(sub)

    def _seed(self, sub: UUID) -> str:
        d = uuid4().hex
        self.drafts[d] = sub
        return d

    def _remember(self, key: tuple[Any, ...], text: str) -> str:
        if KEYWORD in text and "Guarde" in text:
            self.hist[key] = True
            return "ok"
        known = self.hist.get(key) or (self.leaky and any(k[2] == key[2] for k in self.hist))
        return f"era {KEYWORD}" if known and not self.amnesic else "não sei"

    def err(self, code: int) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": code}})

    def a2a(self, req: httpx.Request) -> httpx.Response:
        h = req.headers.get("authorization", "")
        sub = _sub(h[7:] if h.lower().startswith("bearer ") else None, "a2a")
        if self.leaky and h.lower().startswith("bearer "):
            sub = sub or VICTIM  # aceita qualquer token
        if sub is None:
            return httpx.Response(401)
        body = json.loads(req.content)
        m, p = body["method"], body.get("params", {})
        if m == "SendMessage":
            msg = p["message"]
            if "text" not in msg["parts"][0]:
                state = {"state": "TASK_STATE_FAILED"}
                return httpx.Response(200, json={"result": {"task": {"id": "x", "status": state}}})
            t = msg.get("taskId")
            if t and self.tasks.get(t) != sub and not self.leaky:
                return self.err(-32001)
            ctx = msg.get("contextId", "c")
            ans = self._remember((sub, "a2a", ctx), msg["parts"][0]["text"])
            tid = uuid4().hex
            self.tasks[tid] = sub
            task = {"id": tid, "contextId": ctx, "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [{"parts": [{"text": ans}]}]}  # fmt: skip
            return httpx.Response(200, json={"result": {"task": task}})
        if m in ("GetTask", "CancelTask"):
            if self.tasks.get(p.get("id")) != sub and not self.leaky:
                return self.err(-32001)
            return httpx.Response(200, json={"result": {"id": p.get("id")}})
        if m == "ListTasks":
            mine = [{"id": t} for t, o in self.tasks.items() if o == sub or self.leaky]
            return httpx.Response(200, json={"result": {"tasks": mine}})
        if m in ("SendStreamingMessage", "SubscribeToTask", "GetExtendedAgentCard"):
            return self.err(-32004)
        return self.err(-32003)

    def chat(self, req: httpx.Request) -> httpx.Response:
        sub = _sub(req.headers.get("cookie", "")[8:], "chat")
        if sub is None:
            return httpx.Response(401)
        body = json.loads(req.content)
        if len(body["text"]) > 20000:
            return httpx.Response(422)
        trace = uuid4().hex
        self.traces[trace] = sub
        ans = self._remember((sub, "web", body["conversation_id"]), body["text"])
        return httpx.Response(200, text=_sse(ans, trace))

    def gated(self, req: httpx.Request) -> UUID | httpx.Response:
        sub = _sub(req.headers.get("cookie", "")[8:], "chat")
        if sub is None:
            return httpx.Response(401)
        good = req.headers.get("origin") == "http://api"
        if not (good and req.headers["content-type"].startswith("application/json")):
            return httpx.Response(403)
        return sub

    def feedback(self, req: httpx.Request) -> httpx.Response:
        sub = self.gated(req)
        if isinstance(sub, httpx.Response):
            return sub
        owner = self.traces.get(json.loads(req.content)["trace_id"])
        return httpx.Response(200 if owner == sub or self.leaky else 404)

    def confirm(self, req: httpx.Request) -> httpx.Response:
        sub = self.gated(req)
        if isinstance(sub, httpx.Response):
            return sub
        d = json.loads(req.content)["draft_id"]
        if self.drafts.get(d) == sub or (self.leaky and d in self.drafts):
            return httpx.Response(200, json={"sa": "SA-2026-000001"})
        return httpx.Response(409)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        route: dict[str, Callable[[httpx.Request], httpx.Response]] = {
            "/a2a": self.a2a, "/chat": self.chat, "/feedback": self.feedback,
            "/protocols/confirm": self.confirm,
        }  # fmt: skip
        return route.get(req.url.path, lambda _: httpx.Response(404))(req)


async def attack(srv: Server, **kw: Any) -> dict[str, bool]:
    async def seed(sub: UUID, conv: str) -> str:
        return srv._seed(sub)

    res = await run_all(
        "http://api", SECRET, VICTIM, ATTACKER, burst=0, transport=httpx.MockTransport(srv),
        recall_control=True, seed_draft=seed, **kw,
    )  # fmt: skip
    return {r.name: r.defended for r in res}


async def test_secure_server_defends_everything() -> None:
    out = await attack(Server())
    assert all(out.values()), [k for k, v in out.items() if not v]
    expected = {
        "jwt_forjado_a2a", "jwt_forjado_chat", "alg_none_a2a", "alg_none_chat",
        "aud_chat_em_a2a", "aud_a2a_em_chat", "jwt_expirado", "sem_credencial",
        "csrf_sem_origin", "csrf_origin_alheia", "csrf_form", "controle_csrf_origem_propria",
        "a2a_get_task_alheio", "a2a_cancel_task_alheio", "a2a_list_tasks_alheio",
        "a2a_send_task_alheio", "a2a_context_alheio", "a2a_troca_de_canal", "chat_conversa_alheia",
        "feedback_trace_alheio", "confirm_draft_alheio", "a2a_datapart", "entrada_2001",
        "entrada_20001",
    } | {
        f"a2a_{m}" for m in (
            "SendStreamingMessage", "SubscribeToTask", "CreateTaskPushNotificationConfig",
            "GetTaskPushNotificationConfig", "ListTaskPushNotificationConfigs",
            "DeleteTaskPushNotificationConfig", "GetExtendedAgentCard",
        )
    }  # fmt: skip
    assert expected <= set(out), expected - set(out)


async def test_leaky_server_is_flagged_everywhere() -> None:
    out = await attack(Server(leaky=True))
    for name in ("a2a_get_task_alheio", "a2a_list_tasks_alheio", "jwt_forjado_a2a",
                 "a2a_context_alheio", "a2a_troca_de_canal", "chat_conversa_alheia",
                 "feedback_trace_alheio", "confirm_draft_alheio"):  # fmt: skip
        assert not out[name], name
    assert out["controle_dono_get_task"]


async def test_csrf_control_must_be_exactly_409() -> None:
    srv = Server()
    srv.confirm = lambda req: (
        httpx.Response(200) if req.headers.get("origin") == "http://api" else httpx.Response(403)
    )  # type: ignore[method-assign]  # noqa: E501
    assert not (await attack(srv))["controle_csrf_origem_propria"]


async def test_owner_recall_control_aborts_when_owner_forgets() -> None:
    with pytest.raises(RuntimeError, match="controle positivo"):
        await attack(Server(amnesic=True))


async def test_positive_control_fails_if_owner_cannot_read_own_task() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": -32001}})

    with pytest.raises(RuntimeError, match="controle positivo"):
        await run_all("http://api", SECRET, VICTIM, ATTACKER, burst=0,
                      transport=httpx.MockTransport(h))  # fmt: skip
