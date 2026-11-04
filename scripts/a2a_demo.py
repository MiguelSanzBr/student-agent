"""`python -m scripts.a2a_demo [CASO]` (no container da API): token a2a, card e um SendMessage."""

import asyncio
import os
import sys

import httpx
from pydantic import ValidationError

from app.api.auth import issue_token
from app.api.dev import load_golden
from app.config import AppSettings

QUESTION = "quanto falta para quitar?"


async def main(case: str) -> str:
    try:
        s = AppSettings()
    except ValidationError as e:  # o pydantic ecoaria o valor do segredo: só os nomes dos campos
        campos = ", ".join(sorted({str(x["loc"][0]) for x in e.errors() if x["loc"]}))
        raise SystemExit(f"configuração inválida: {campos}") from None
    pk = load_golden(s.etl_out_dir / "golden_login.json").get(case)
    if pk is None:
        raise SystemExit(f"caso {case!r} não é golden")
    base = os.environ.get("A2A_DEMO_URL", "http://localhost:8000")
    headers = {"authorization": f"Bearer {issue_token(pk, 'a2a', s)}", "a2a-version": "1.0"}
    async with httpx.AsyncClient(base_url=base, timeout=60) as c:
        card = (await c.get("/.well-known/agent-card.json")).json()
        print("card:", card["name"], "| skills:", [k["id"] for k in card["skills"]])
        body = {"jsonrpc": "2.0", "id": 1, "method": "SendMessage",
                "params": {"message": {"messageId": "demo-1", "role": "ROLE_USER",
                                       "parts": [{"text": QUESTION}]}}}  # fmt: skip
        r = (await c.post("/a2a", json=body, headers=headers)).json()
        lst = {"jsonrpc": "2.0", "id": 2, "method": "ListTasks", "params": {}}
        tasks = (await c.post("/a2a", json=lst, headers=headers)).json()["result"]["tasks"]
    task = r["result"]["task"]
    assert task["id"] in [t["id"] for t in tasks], "ListTasks não devolveu a tarefa do dono"
    return str(task["artifacts"][0]["parts"][0]["text"])


if __name__ == "__main__":
    print(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "4")))
