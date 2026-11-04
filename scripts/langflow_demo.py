"""`python -m scripts.langflow_demo < flow.json` (no container da API): importa o fluxo no Langflow,
roda uma pergunta do golden 4 por ele e imprime o JSON da evidência (sem token)."""

import json
import os
import sys

import httpx
from pydantic import ValidationError

from app.api.auth import issue_token
from app.api.dev import load_golden
from app.config import AppSettings

QUESTION = "quanto falta para quitar?"
EXPECT = "R$ 49.508,43"  # valor que só a nossa API (replay) devolve para o golden 4


def main() -> dict[str, str]:
    try:
        s = AppSettings()
    except ValidationError as e:  # o pydantic ecoaria o valor do segredo: só os nomes dos campos
        campos = ", ".join(sorted({str(x["loc"][0]) for x in e.errors() if x["loc"]}))
        raise SystemExit(f"configuração inválida: {campos}") from None
    pk = load_golden(s.etl_out_dir / "golden_login.json").get("4")
    if pk is None:
        raise SystemExit("caso '4' não é golden")
    flow = json.load(sys.stdin)
    url = os.environ.get("LANGFLOW_URL", "http://langflow:7860")
    with httpx.Client(base_url=url, timeout=120) as c:
        auth = {"authorization": "Bearer " + c.get("/api/v1/auto_login").json()["access_token"]}
        for v in c.get("/api/v1/variables/", headers=auth).json():  # reexecução com KEEP=1
            if v["name"] == "A2A_TOKEN":
                c.delete(f"/api/v1/variables/{v['id']}", headers=auth).raise_for_status()
        c.post("/api/v1/variables/", headers=auth, json={
            "name": "A2A_TOKEN", "value": issue_token(pk, "a2a", s), "type": "Credential",
            "default_fields": []}).raise_for_status()  # fmt: skip
        fid = c.post("/api/v1/flows/", headers=auth, json=flow).raise_for_status().json()["id"]
        lf_key = c.post("/api/v1/api_key/", headers=auth, json={"name": "demo"}).json()["api_key"]
        run = c.post(f"/api/v1/run/{fid}", headers={"x-api-key": lf_key}, json={
            "input_value": QUESTION, "input_type": "chat", "output_type": "chat"})  # fmt: skip
        run.raise_for_status()
    msg = run.json()["outputs"][0]["outputs"][0]["results"]["message"]["text"]
    if EXPECT not in msg:
        raise SystemExit(f"resposta não veio do agente (esperado {EXPECT!r}): {msg[:300]!r}")
    return {"question": QUESTION, "answer": msg, "via": "Langflow A2A Agent -> /a2a (x-api-key)"}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
