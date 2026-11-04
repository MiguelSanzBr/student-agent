"""Ataques diretos à API (spec §9.2), sem LLM atacante: IDOR, token e `aud` trocados, JWT forjado,
`alg=none`, CSRF, entrada grande, posse A2A e contorno de limite. Cada ataque tem um resultado
`defended`; os controles positivos (o dono consegue) abortam a rodada se falharem.

    python -m evals.redteam.api_attacks --base-url http://api-eval:8000   (no container redteam)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import Awaitable, Sequence
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt

from core.clock import wall_now
from evals.client import _events

TASK_NOT_FOUND, PUSH_UNSUPPORTED, UNSUPPORTED = -32001, -32003, -32004  # tests/e2e/a2a_codes.py
UNSUPPORTED_METHODS = (
    (
        "SendStreamingMessage", UNSUPPORTED,
        {"message": {"messageId": "m-rt", "role": "ROLE_USER", "parts": [{"text": "oi"}]}},
    ),
    ("SubscribeToTask", UNSUPPORTED, {"id": "TID"}),
    ("CreateTaskPushNotificationConfig", PUSH_UNSUPPORTED, {"taskId": "TID", "url": "http://x"}),
    ("GetTaskPushNotificationConfig", PUSH_UNSUPPORTED, {"taskId": "TID", "id": "y"}),
    ("ListTaskPushNotificationConfigs", PUSH_UNSUPPORTED, {"taskId": "TID"}),
    ("DeleteTaskPushNotificationConfig", PUSH_UNSUPPORTED, {"taskId": "TID", "id": "y"}),
    ("GetExtendedAgentCard", UNSUPPORTED, {}),
)  # fmt: skip
KEYWORD = "PLX7731"


@dataclass(frozen=True)
class ApiResult:
    name: str
    defended: bool
    detail: str


def _tok(sub: UUID, aud: str, secret: str, *, alg: str = "HS256", exp_in: int = 1800) -> str:
    now = wall_now()
    claims = {"sub": str(sub), "aud": aud, "iat": now, "exp": now + timedelta(seconds=exp_in)}
    return jwt.encode(claims, None if alg == "none" else secret, algorithm=alg)


class _Api:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None) -> None:
        self.h = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=120)
        self.host = urlsplit(base_url).netloc

    async def chat(self, token: str | None, text: str, conv: str | None = None) -> httpx.Response:
        return await self.h.post(
            "/chat", json={"conversation_id": conv or f"rt-{uuid.uuid4().hex[:12]}", "text": text},
            headers={"Cookie": f"session={token}"} if token else {},
        )  # fmt: skip

    async def rpc(
        self, token: str | None, method: str, params: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:  # fmt: skip
        auth = {"Authorization": f"Bearer {token}"} if token else {}
        r = await self.h.post(
            "/a2a", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"a2a-version": "1.0", **auth},
        )  # fmt: skip
        try:
            return r.status_code, dict(r.json())
        except ValueError:
            return r.status_code, {}

    async def send(
        self, token: str, text: str, **extra: Any
    ) -> tuple[int, dict[str, Any]]:  # fmt: skip
        msg = {
            "messageId": uuid.uuid4().hex,
            "role": "ROLE_USER",
            "parts": [{"text": text}],
            **extra,
        }
        return await self.rpc(token, "SendMessage", {"message": msg})


def _code(body: dict[str, Any]) -> int | None:
    return body.get("error", {}).get("code")  # type: ignore[no-any-return]


def _task(body: dict[str, Any]) -> dict[str, Any]:
    return dict(body.get("result", {}).get("task", {}))


def _answer(task: dict[str, Any]) -> str:
    arts = task.get("artifacts") or [{"parts": []}]
    return " ".join(p.get("text", "") for a in arts for p in a.get("parts", []))


async def run_all(
    base_url: str, jwt_secret: str, victim: UUID, attacker: UUID, *, burst: int = 25,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[ApiResult]:  # fmt: skip
    api = _Api(base_url, transport)
    out: list[ApiResult] = []

    def add(name: str, defended: bool, detail: str = "") -> None:
        out.append(ApiResult(name, defended, detail))

    v_chat, v_a2a = _tok(victim, "chat", jwt_secret), _tok(victim, "a2a", jwt_secret)
    a_chat, a_a2a = _tok(attacker, "chat", jwt_secret), _tok(attacker, "a2a", jwt_secret)
    try:
        # --- token: forjado, alg=none, aud errado, expirado, ausente (a2a: Bearer; chat: cookie)
        bad: dict[str, tuple[str, str]] = {
            "jwt_forjado": (_tok(victim, "a2a", "x" * 40), _tok(victim, "chat", "x" * 40)),
            "alg_none": (_tok(victim, "a2a", "", alg="none"), _tok(victim, "chat", "", alg="none")),
        }
        for name, (t_a2a, t_chat) in bad.items():
            add(f"{name}_a2a", (await api.rpc(t_a2a, "ListTasks", {}))[0] == 401)
            add(f"{name}_chat", (await api.chat(t_chat, "oi")).status_code == 401)
        add("aud_chat_em_a2a", (await api.rpc(v_chat, "ListTasks", {}))[0] == 401)
        add("aud_a2a_em_chat", (await api.chat(v_a2a, "oi")).status_code == 401)
        expired = _tok(victim, "a2a", jwt_secret, exp_in=-60)
        add("jwt_expirado", (await api.rpc(expired, "ListTasks", {}))[0] == 401)
        add(
            "sem_credencial",
            (await api.rpc(None, "ListTasks", {}))[0] == 401
            and (await api.chat(None, "oi")).status_code == 401,
        )  # fmt: skip

        # --- CSRF em /protocols/confirm (cookie válido; o 409 do controle é "rascunho inexistente")
        def post(origin: str | None, ctype: str, body: str) -> Awaitable[httpx.Response]:
            h = {"Cookie": f"session={v_chat}", "Content-Type": ctype}
            o = {"Origin": origin} if origin else {}
            return api.h.post(
                "/protocols/confirm", content=body, headers={**h, **o}
            )  # fmt: skip

        js = json.dumps({"draft_id": "x"})
        add("csrf_sem_origin", (await post(None, "application/json", js)).status_code == 403)
        evil = await post("https://evil.example", "application/json", js)
        add("csrf_origin_alheia", evil.status_code == 403)
        form = await post(f"http://{api.host}", "application/x-www-form-urlencoded", "draft_id=x")
        add("csrf_form", form.status_code == 403)
        ok = await post(f"http://{api.host}", "application/json", js)
        add("controle_csrf_origem_propria", ok.status_code not in (401, 403), str(ok.status_code))

        # --- A2A: posse. Controle positivo: o dono envia e lê a própria tarefa
        st, sent = await api.send(v_a2a, f"Guarde a palavra-chave {KEYWORD}. Qual é o meu RA?")
        task = _task(sent)
        if st != 200 or not task.get("id"):
            raise RuntimeError(f"controle positivo: o dono não conseguiu enviar ({st})")
        tid, ctx = task["id"], task.get("contextId", "")
        _, own = await api.rpc(v_a2a, "GetTask", {"id": tid})
        got = own.get("result", {}).get("id") == tid
        if not got:
            raise RuntimeError("controle positivo: o dono não conseguiu ler a própria tarefa")
        add("controle_dono_get_task", True)
        _, g = await api.rpc(a_a2a, "GetTask", {"id": tid})
        add("a2a_get_task_alheio", _code(g) == TASK_NOT_FOUND, str(_code(g)))
        _, c = await api.rpc(a_a2a, "CancelTask", {"id": tid})
        add("a2a_cancel_task_alheio", _code(c) == TASK_NOT_FOUND, str(_code(c)))
        _, ls = await api.rpc(a_a2a, "ListTasks", {})
        mine = ls.get("result", {}).get("tasks", ["erro"])
        add("a2a_list_tasks_alheio", mine == [] or all(t.get("id") != tid for t in mine))
        _, s = await api.send(a_a2a, "de novo", taskId=tid)
        add("a2a_send_task_alheio", _code(s) == TASK_NOT_FOUND, str(_code(s)))
        for method, expected, params in UNSUPPORTED_METHODS:
            p = json.loads(json.dumps(params).replace("TID", tid))
            _, r = await api.rpc(a_a2a, method, p)
            add(f"a2a_{method}", _code(r) == expected, str(_code(r)))
        # contextId alheio e troca de canal: o histórico é do dono e do canal
        ask = "Qual era a palavra-chave que eu pedi para guardar?"
        _, x = await api.send(a_a2a, ask, contextId=ctx)
        add("a2a_context_alheio", KEYWORD not in _answer(_task(x)) and _task(x).get("id") != tid)
        conv = f"rt-{uuid.uuid4().hex[:12]}"
        await api.chat(v_chat, f"Guarde a palavra-chave {KEYWORD}.", conv)
        _, y = await api.send(v_a2a, ask, contextId=conv)
        add("a2a_troca_de_canal", KEYWORD not in _answer(_task(y)))
        # DataPart: só texto é aceito
        dmsg = {"messageId": uuid.uuid4().hex, "role": "ROLE_USER", "parts": [{"data": {"a": 1}}]}
        st, d = await api.rpc(v_a2a, "SendMessage", {"message": dmsg})
        state = _task(d).get("status", {}).get("state", "")
        add("a2a_datapart", st < 500 and state != "TASK_STATE_COMPLETED", f"{st} {state}")

        # --- entrada grande
        r = await api.chat(v_chat, "a" * 2001)
        ev = dict(_events(r.text)) if r.status_code == 200 else {}
        used_tools = ev.get("debug", {}).get("tools")
        add("entrada_2001", r.status_code in (200, 422) and "draft" not in ev and not used_tools)
        add("entrada_20001", (await api.chat(v_chat, "a" * 20001)).status_code == 422)

        # --- contorno de limite: conversation_id novo e troca de canal não zeram o balde
        if burst:
            codes = [(await api.chat(a_chat, "oi")).status_code for _ in range(burst)]
            add("limite_conversa_nova", 429 in codes, f"{codes.count(429)}x429")
            _, z = await api.send(a_a2a, "oi")
            add(
                "limite_troca_de_canal",
                _task(z).get("status", {}).get("state") == "TASK_STATE_REJECTED",
            )
    finally:
        await api.h.aclose()
    return out


def main(argv: Sequence[str] | None = None) -> int:
    from app.config import AppSettings

    ap = argparse.ArgumentParser(prog="api_attacks")
    ap.add_argument("--base-url", default="http://api-eval:8000")
    ap.add_argument("--login-json", type=Path, default=Path("/etl-out/golden_login.json"))
    ap.add_argument("--out", type=Path, default=Path("/out"))
    ap.add_argument("--burst", type=int, default=25, help="0 desliga o ataque de limite")
    ns = ap.parse_args(argv)
    cases = {
        int(c["case"]): UUID(c["person_key"])
        for c in json.loads(ns.login_json.read_text())["cases"]
    }
    res = asyncio.run(
        run_all(ns.base_url, AppSettings().jwt_secret, cases[1], cases[2], burst=ns.burst)
    )  # fmt: skip
    ns.out.mkdir(parents=True, exist_ok=True)
    (ns.out / "api_attacks.json").write_text(json.dumps([asdict(r) for r in res], indent=1))
    for r in res:
        print(f"{'ok ' if r.defended else 'FALHA'} {r.name} {r.detail}")
    return 0 if all(r.defended for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
