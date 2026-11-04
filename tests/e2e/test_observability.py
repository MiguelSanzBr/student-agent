import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import metrics


def test_metrics_endpoint_exposes_prometheus_text() -> None:
    app = FastAPI()
    app.include_router(metrics.router)
    r = TestClient(app).get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert "agent_turns_total" in r.text or "# HELP" in r.text


@pytest.mark.skipif(not os.environ.get("PHOENIX_URL"), reason="sem Phoenix (profile obs)")
@pytest.mark.usefixtures("app_secrets")
async def test_phoenix_span_and_annotation_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Smoke (PHOENIX_URL): turno -> spans no Phoenix -> anotação -> export."""
    from phoenix.client import Client

    from app.config import AppSettings
    from app.obs import annotations
    from app.obs.tracing import setup_tracing
    from scripts.phoenix_export import export_phoenix, import_phoenix
    from tests.unit.turn_env import OK, SCOPE, make_service, patch_db, scripted

    url = os.environ["PHOENIX_URL"]
    project = f"smoke-{uuid4().hex[:8]}"
    patch_db(monkeypatch)
    s = AppSettings(otel_exporter_otlp_endpoint=f"{url}/v1/traces", phoenix_project=project)
    provider = setup_tracing(s)
    svc = make_service(s, scripted(OK))
    evs: list[dict[str, Any]] = []

    async def emit(e: Mapping[str, Any]) -> None:
        evs.append(dict(e))

    adm = await svc.admit(scope=SCOPE, channel="web")
    await svc.handle(admission=adm, scope=SCOPE, conversation_id="c", text="oi", channel="web",
                     emit=emit)  # fmt: skip
    await svc.settle(adm, None)
    annotations.annotate(evs[0]["trace_id"], "thumb", "down", 0.0, "smoke")
    await annotations.drain()
    client = Client(base_url=url)
    spans = client.spans.get_spans(project_identifier=project, limit=50)
    assert {sp["name"] for sp in spans} >= {"chat.turn", "agent.call_model", "agent.guard"}
    root = next(sp for sp in spans if sp["name"] == "chat.turn")
    notes = client.spans.get_span_annotations(
        span_ids=[root["context"]["span_id"]], project_identifier=project
    )
    assert [n["name"] for n in notes] == ["thumb"]
    out = Path(os.environ.get("PHOENIX_EXPORT_DIR", "/tmp/phoenix-smoke"))
    export_phoenix(project, out)
    assert (out / f"{project}.json").exists() and import_phoenix  # reimport: make phoenix-import
    provider.shutdown()
