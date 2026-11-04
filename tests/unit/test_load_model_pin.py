"""O teste de carga usa um único modelo: o da API (compose.load.yml) e o do runner (Makefile)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_load_api_and_runner_share_load_model() -> None:
    compose = (ROOT / "ops/compose.load.yml").read_text()
    mk = (ROOT / "Makefile").read_text()
    assert re.search(r"LLM_MODEL:\s*\$\{LOAD_MODEL:-claude-haiku-5-5\}", compose)
    assert "LOAD_MODEL ?= claude-haiku-5-5" in mk
    target = mk.split("\nload-test:")[1].split("\n\n")[0]
    assert "--model $(LOAD_MODEL)" in target
    assert "LOAD_MODEL=$(LOAD_MODEL)" in mk  # exportado ao compose (LDC)
