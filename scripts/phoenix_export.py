"""Export/import de uma rodada do Phoenix (projeto = run_id): spans + anotações em um JSON.

`python -m scripts.phoenix_export export <run_id> [out_dir]` e `... import <arquivo.json>`;
URL do Phoenix em PHOENIX_URL (padrão http://127.0.0.1:6006).
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Protocol


class PhoenixIO(Protocol):
    def get_spans(self, project: str) -> list[dict[str, Any]]: ...
    def get_annotations(self, project: str, span_ids: list[str]) -> list[dict[str, Any]]: ...
    def log_spans(self, project: str, spans: list[dict[str, Any]]) -> None: ...
    def log_annotations(self, notes: list[dict[str, Any]]) -> None: ...


class _RealPhoenix:
    def __init__(self, url: str) -> None:
        from phoenix.client import Client

        self.c = Client(base_url=url)

    def get_spans(self, project: str) -> list[dict[str, Any]]:
        return [dict(s) for s in self.c.spans.get_spans(project_identifier=project, limit=100_000)]

    def get_annotations(self, project: str, span_ids: list[str]) -> list[dict[str, Any]]:
        out = self.c.spans.get_span_annotations(span_ids=span_ids, project_identifier=project)
        return [dict(a) for a in out]

    def log_spans(self, project: str, spans: list[dict[str, Any]]) -> None:
        self.c.spans.log_spans(project_identifier=project, spans=spans)  # type: ignore[arg-type]

    def log_annotations(self, notes: list[dict[str, Any]]) -> None:
        keep = ("span_id", "name", "annotator_kind", "result", "metadata", "identifier")
        data = [{k: n[k] for k in keep if k in n} for n in notes]
        for i in range(10):  # o Phoenix ingere os spans de forma assíncrona: 404 até aparecerem
            try:
                self.c.spans.log_span_annotations(span_annotations=data, sync=True)  # type: ignore[arg-type]
                return
            except Exception:  # noqa: BLE001
                if i == 9:
                    raise
                time.sleep(1)


def _client(client: PhoenixIO | None) -> PhoenixIO:
    return client or _RealPhoenix(os.environ.get("PHOENIX_URL", "http://127.0.0.1:6006"))


def export_phoenix(run_id: str, out: Path, client: PhoenixIO | None = None) -> None:
    c = _client(client)
    spans = c.get_spans(run_id)
    ids = [s["context"]["span_id"] for s in spans]
    out.mkdir(parents=True, exist_ok=True)
    data = {"run_id": run_id, "spans": spans, "annotations": c.get_annotations(run_id, ids)}
    (out / f"{run_id}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))


def import_phoenix(path: Path, client: PhoenixIO | None = None) -> None:
    data = json.loads(path.read_text())
    c = _client(client)
    c.log_spans(data["run_id"], data["spans"])
    c.log_annotations(data["annotations"])


if __name__ == "__main__":
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "export":
        export_phoenix(arg, Path(sys.argv[3] if len(sys.argv) > 3 else "docs/evidence/phoenix"))
    else:
        import_phoenix(Path(arg))
