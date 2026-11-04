"""Renderiza os números das evidências nos marcadores `<!-- num:chave -->…<!-- /num -->`."""

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
DOCS = [ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md", EVIDENCE / "README.md"]

MARKER = re.compile(r"<!-- num:([\w.\-]+) -->(.*?)<!-- /num -->", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
# sem \w antes: "D17" e "T22" são identificadores, não números
TOKEN = re.compile(r"(?<!\w)-?\d[\d.,]*\d|(?<!\w)\d")
# evidências que chegam depois do rascunho: achatadas como `<arquivo>.<caminho>`
LATER = ("quality", "guard", "security", "latency", "observability")


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _flatten(prefix: str, value: Any, out: dict[str, str]) -> None:
    if isinstance(value, Mapping):
        for k, v in value.items():
            _flatten(f"{prefix}.{k}", v, out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}.{i}", v, out)
    elif value is not None and not isinstance(value, bool):
        out[prefix] = str(value)


def build_numbers(evidence_dir: Path) -> dict[str, str]:
    """Chaves estáveis → texto. Arquivo ausente = chaves ausentes (render_docs reclama)."""
    out: dict[str, str] = {}
    if (dq := _load(evidence_dir / "data-quality.json")) is not None:
        checks = {c["name"]: c["memory"] for c in dq["checks"]}
        out |= {
            "data.persons": str(checks["pessoas"]),
            "data.enrollments": str(checks["matrículas"]),
            "data.contracts": str(checks["contratos resolvidos"]),
            "data.protocols_linked": str(checks["protocolos ligados"]),
            "data.protocols_unlinked": str(dq["protocols_unlinked"]),
            "data.lms_accounts": str(checks["contas LMS ligadas"]),
            "data.protocol_ref": str(dq["protocol_ref"][0]),
            "data.snapshot": str(dq["snapshot"][0]),
            "data.logins_absent": str(dq["logins_absent"]),
            "data.lms_attempts": str(dq["lms"]["tentativas"]),
            "data.simulados_abertos": str(dq["lms"]["simulados_abertos"]),
            "data.simulados_restantes": str(dq["lms"]["simulados_com_tentativa_restante"]),
            "data.provas_sem_tentativa": str(dq["lms"]["encerradas_sem_tentativa"]),
        }
        out |= {f"data.rule.{k}": str(v) for k, v in dq["protocols_by_rule"].items()}
    if (sp := _load(evidence_dir / "spikes.json")) is not None:
        for name, code in sp["a2a"]["codes"].items():
            out[f"a2a.{re.sub(r'[^\w]+', '_', name).strip('_')}"] = str(code)
    for stem in LATER:
        if (data := _load(evidence_dir / f"{stem}.json")) is not None:
            _flatten(stem, data, out)
    return out


def render_docs(paths: Sequence[Path], numbers: Mapping[str, str]) -> None:
    """Troca o miolo de cada marcador. Chave ausente → KeyError antes de gravar qualquer arquivo."""
    texts = {p: p.read_text() for p in paths}
    for p, text in texts.items():
        for key in MARKER.findall(text):
            if key[0] not in numbers:
                raise KeyError(f"{p.name}: chave sem número: {key[0]}")
    for p, text in texts.items():
        p.write_text(MARKER.sub(lambda m: f"<!-- num:{m[1]} -->{numbers[m[1]]}<!-- /num -->", text))


def _norm(s: str) -> str:
    return re.sub(r"[.,]", "", s)


def find_hand_typed(text: str, numbers: Mapping[str, str]) -> list[str]:
    """Tokens numéricos fora de marcador iguais a algum valor de `numbers` (≥ 2 dígitos)."""
    forbidden = {
        _norm(v)
        for v in numbers.values()
        if re.fullmatch(r"-?\d[\d.,]*", v) and len(re.sub(r"\D", "", v)) >= 2
    }
    bare = COMMENT.sub("", MARKER.sub("", text))
    return [t for t in TOKEN.findall(bare) if _norm(t) in forbidden]


def main() -> int:
    numbers = build_numbers(EVIDENCE)
    (EVIDENCE / "numbers.json").write_text(
        json.dumps(numbers, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    render_docs(DOCS, numbers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
