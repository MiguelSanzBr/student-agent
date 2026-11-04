"""Cliente HTTP do runner: `/chat` (SSE), `/protocols/confirm`, `/feedback` e `/a2a` (JSON-RPC).

Contrato SSE assumido (Task 25): `answer` {text}; `draft` {draft_id, ...}; `meta`/`done`
{trace_id, status}; `debug` (dev+eval) {tools: [nome], pre_guard_answer, cost_degraded}.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx

from evals.golden.score import TurnRecord


class Rejected(Exception):
    """Admissão recusada antes do stream (429/503): não é tentativa; espere `retry_after`."""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"HTTP {status}")
        self.status, self.retry_after = status, retry_after


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for block in text.split("\n\n"):
        ev, data = "message", []
        for ln in block.splitlines():
            if ln.startswith("event:"):
                ev = ln[6:].strip()
            elif ln.startswith("data:"):
                data.append(ln[5:].strip())
        if data:
            out.append((ev, json.loads("\n".join(data))))
    return out


class ChatClient:
    def __init__(
        self, base_url: str, *, transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:  # fmt: skip
        self._http = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    @staticmethod
    def _cookie(token: str) -> dict[str, str]:
        return {"Cookie": f"session={token}"}

    async def ask(self, token: str, conversation_id: str, text: str) -> TurnRecord:
        r = await self._http.post(
            "/chat", json={"conversation_id": conversation_id, "text": text},
            headers=self._cookie(token),
        )  # fmt: skip
        if r.status_code in (429, 503):
            ra = r.headers.get("Retry-After")
            raise Rejected(r.status_code, float(ra) if ra else None)
        r.raise_for_status()
        answer, tools, draft, trace_id, status = "", (), False, "", "ok"
        pre, degraded = None, False
        for ev, d in _events(r.text):
            if ev == "answer":
                answer = d.get("text", "")
            elif ev == "draft":
                draft = True
            elif ev == "meta":
                trace_id = d.get("trace_id", trace_id)
            elif ev == "debug":
                tools = tuple(d.get("tools", ()))
                pre, degraded = d.get("pre_guard_answer"), bool(d.get("cost_degraded"))
            elif ev == "done":
                status, trace_id = d.get("status", status), d.get("trace_id", trace_id)
            elif ev == "error":
                status = "error"
        return TurnRecord(answer, tools, draft, status, trace_id, pre, degraded)

    async def confirm(self, token: str, draft_id: str) -> dict[str, Any]:
        r = await self._http.post(
            "/protocols/confirm", json={"draft_id": draft_id}, headers=self._cookie(token)
        )
        r.raise_for_status()
        return dict(r.json())

    async def a2a_send(self, token: str, context_id: str, text: str) -> dict[str, Any]:
        msg = {"messageId": uuid4().hex, "contextId": context_id, "role": "ROLE_USER",
               "parts": [{"text": text}]}  # fmt: skip
        body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": msg}}
        r = await self._http.post("/a2a", json=body, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return dict(r.json())

    async def feedback(self, token: str, trace_id: str, useful: bool) -> None:
        r = await self._http.post(
            "/feedback", json={"trace_id": trace_id, "useful": useful},
            headers=self._cookie(token),
        )  # fmt: skip
        r.raise_for_status()
