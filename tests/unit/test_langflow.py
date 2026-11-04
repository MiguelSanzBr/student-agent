"""Lane Langflow: imagem fixada, porta local, fluxo sem segredo (T27 "should")."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "ops/langflow/flow.json"


def _config() -> dict:
    out = subprocess.run(
        ["docker", "compose", "--profile", "langflow", "config", "--format", "json"],
        cwd=ROOT, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/usr/local/bin", "HOME": "/tmp"},
    ).stdout  # fmt: skip
    return json.loads(out)["services"]["langflow"]


def test_compose_langflow_pinned_local_no_project_mounts() -> None:
    svc = _config()
    assert re.fullmatch(r"langflowai/langflow:1\.(1[1-9]|[2-9]\d)\.\d+", svc["image"]), svc["image"]
    assert svc["profiles"] == ["langflow"]
    ports = svc["ports"]
    assert ports and all(p["host_ip"] == "127.0.0.1" for p in ports)
    assert svc["environment"]["LANGFLOW_SSRF_ALLOWED_HOSTS"] == "api"
    assert not svc.get("volumes")  # nem data/ nem segredos do projeto


def _nodes() -> list[dict]:
    data = json.loads(FLOW.read_text())
    return (data.get("data") or data)["nodes"]


def test_flow_has_chat_a2a_chat() -> None:
    types = sorted(n["data"]["type"] for n in _nodes())
    assert types == ["A2AAgent", "ChatInput", "ChatOutput"]


def test_flow_has_no_secret() -> None:
    raw = FLOW.read_text()
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}\.", raw)  # JWT
    a2a = next(n for n in _nodes() if n["data"]["type"] == "A2AAgent")
    key = a2a["data"]["node"]["template"]["api_key"]
    assert key["load_from_db"] is True and key["value"] == "A2A_TOKEN"  # variável global
    url = a2a["data"]["node"]["template"]["agent_url"]["value"]
    assert url.startswith("http://api:8000")
