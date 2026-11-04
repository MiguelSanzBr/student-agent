import json
from uuid import UUID, uuid4

import httpx
import jwt
import pytest

from evals.redteam.api_attacks import run_all

SECRET = "s" * 40
SSE_OK = (
    'event: answer\ndata: {"text": "Não posso ajudar com isso."}\n\n'
    'event: done\ndata: {"status": "ok"}\n\n'
)
VICTIM, ATTACKER = uuid4(), uuid4()


def _sub(auth: str | None, aud: str, secure: bool = True) -> UUID | None:
    if not auth:
        return None
    try:
        c = jwt.decode(auth, SECRET, algorithms=["HS256"], audience=aud)
        return UUID(c["sub"])
    except Exception:
        return None


def make_handler(leaky: bool = False):  # type: ignore[no-untyped-def]
    tasks: dict[str, UUID] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/a2a":
            h = req.headers.get("authorization", "")
            sub = _sub(h[7:] if h.lower().startswith("bearer ") else None, "a2a")
            if leaky and h.lower().startswith("bearer "):
                sub = sub or VICTIM  # aceita qualquer token
            if sub is None:
                return httpx.Response(401, json={"detail": "x"})
            body = json.loads(req.content)
            m, p = body["method"], body.get("params", {})

            def err(c: int) -> httpx.Response:
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": c}})

            if m == "SendMessage":
                parts = p["message"]["parts"]
                t = p["message"].get("taskId")
                if t and tasks.get(t) != sub and not leaky:
                    return err(-32001)
                if "text" not in parts[0]:
                    return httpx.Response(
                        200,
                        json={
                            "result": {
                                "task": {
                                    "id": "x",
                                    "status": {
                                        "state": "TASK_STATE_FAILED",
                                        "message": {
                                            "parts": [
                                                {"text": "Envie uma única mensagem de texto."}
                                            ]
                                        },
                                    },
                                }
                            }
                        },
                    )
                tid = uuid4().hex
                tasks[tid] = sub
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "task": {
                                "id": tid,
                                "contextId": p["message"].get("contextId", "c"),
                                "status": {"state": "TASK_STATE_COMPLETED"},
                            }
                        }
                    },
                )
            if m in ("GetTask", "CancelTask"):
                tid = p.get("id")
                if tasks.get(tid) != sub and not leaky:
                    return err(-32001)
                return httpx.Response(200, json={"result": {"id": tid}})
            if m == "ListTasks":
                return httpx.Response(
                    200,
                    json={
                        "result": {
                            "tasks": [{"id": t} for t, o in tasks.items() if o == sub or leaky]
                        }
                    },
                )
            if m in ("SendStreamingMessage", "SubscribeToTask", "GetExtendedAgentCard"):
                return err(-32004)
            return err(-32003)
        if path == "/chat":
            tok = req.headers.get("cookie", "")[8:]
            if _sub(tok, "chat") is None:
                return httpx.Response(401)
            n = len(json.loads(req.content)["text"])
            if n > 20000:
                return httpx.Response(422)
            return httpx.Response(
                200,
                text=SSE_OK,
            )
        if path == "/protocols/confirm":
            if _sub(req.headers.get("cookie", "")[8:], "chat") is None:
                return httpx.Response(401)
            ok = req.headers.get("origin") == "http://api" and req.headers[
                "content-type"
            ].startswith("application/json")
            return httpx.Response(409 if ok else 403)
        return httpx.Response(404)

    return handler


async def attack(leaky: bool = False) -> dict[str, bool]:
    res = await run_all(
        "http://api", SECRET, VICTIM, ATTACKER, burst=0,
        transport=httpx.MockTransport(make_handler(leaky)),
    )  # fmt: skip
    return {r.name: r.defended for r in res}


async def test_secure_server_defends_everything() -> None:
    out = await attack()
    assert all(out.values()), [k for k, v in out.items() if not v]
    expected = {
        "jwt_forjado_a2a",
        "jwt_forjado_chat",
        "alg_none_a2a",
        "alg_none_chat",
        "aud_chat_em_a2a",
        "aud_a2a_em_chat",
        "jwt_expirado",
        "sem_credencial",
        "csrf_sem_origin",
        "csrf_origin_alheia",
        "csrf_form",
        "a2a_get_task_alheio",
        "a2a_cancel_task_alheio",
        "a2a_list_tasks_alheio",
        "a2a_send_task_alheio",
        "a2a_context_alheio",
        "a2a_datapart",
        "entrada_2001",
        "entrada_20001",
    } | {
        f"a2a_{m}"
        for m in (
            "SendStreamingMessage",
            "SubscribeToTask",
            "CreateTaskPushNotificationConfig",
            "GetTaskPushNotificationConfig",
            "ListTaskPushNotificationConfigs",
            "DeleteTaskPushNotificationConfig",
            "GetExtendedAgentCard",
        )
    }
    assert expected <= set(out), expected - set(out)


async def test_leaky_server_is_flagged_and_positive_control_present() -> None:
    out = await attack(leaky=True)
    assert not out["a2a_get_task_alheio"] and not out["a2a_list_tasks_alheio"]
    assert not out["jwt_forjado_a2a"]
    assert out["controle_dono_get_task"]


async def test_positive_control_fails_if_owner_cannot_read_own_task() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": -32001}})

    with pytest.raises(RuntimeError, match="controle positivo"):
        await run_all("http://api", SECRET, VICTIM, ATTACKER, burst=0,
                      transport=httpx.MockTransport(h))  # fmt: skip
