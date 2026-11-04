import json
from pathlib import Path
from typing import Any

from scripts.phoenix_export import export_phoenix, import_phoenix


class FakePhoenix:
    def __init__(self) -> None:
        self.spans: dict[str, list[dict[str, Any]]] = {}
        self.notes: list[dict[str, Any]] = []

    def get_spans(self, project: str) -> list[dict[str, Any]]:
        return self.spans.get(project, [])

    def get_annotations(self, project: str, span_ids: list[str]) -> list[dict[str, Any]]:
        return [a for a in self.notes if a["span_id"] in span_ids]

    def log_spans(self, project: str, spans: list[dict[str, Any]]) -> None:
        self.spans.setdefault(project, []).extend(spans)

    def log_annotations(self, notes: list[dict[str, Any]]) -> None:
        self.notes += notes


def test_export_import_roundtrip(tmp_path: Path) -> None:
    src, dst = FakePhoenix(), FakePhoenix()
    src.spans["run1"] = [{"name": "chat.turn", "context": {"span_id": "a", "trace_id": "t"}}]
    src.notes = [{"span_id": "a", "name": "thumb", "label": "down", "score": 0.0}]
    out = tmp_path / "phoenix"
    export_phoenix("run1", out, client=src)
    assert json.loads((out / "run1.json").read_text())["spans"][0]["name"] == "chat.turn"
    import_phoenix(out / "run1.json", client=dst)
    assert dst.spans["run1"] == src.spans["run1"] and dst.notes == src.notes
