"""Planta marcadores em toda célula de coluna `text` dos CSV (teste do marcador, spec §5.6)."""

import csv
import json
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from core.ontology import Ontology


def _plant(cell: str, marker: str) -> str:
    """JSON continua JSON (chave extra), o resto ganha o marcador no fim."""
    try:
        doc = json.loads(cell)
    except ValueError:
        return cell + marker
    if isinstance(doc, dict):
        return json.dumps({**doc, "mk": marker.strip()}, ensure_ascii=False)
    return cell + marker


def plant_marker(src: Path, dst: Path, markers: tuple[str, ...], onto: Ontology) -> int:
    """Copia `src` (pasta `<sistema>/<tabela>.csv`) para `dst` com os marcadores acrescentados a
    toda célula não vazia de coluna `type: text`; devolve o número de células plantadas."""
    return _plant_all(src, dst, lambda _: " " + " ".join(markers), onto)


def plant_payloads(src: Path, dst: Path, payloads: Sequence[str], onto: Ontology) -> int:
    """Como `plant_marker`, mas cada célula de texto recebe a próxima injeção de `payloads`
    (em rodízio): imitação dos modelos de mensagem, homóglifos, caracteres invisíveis."""
    return _plant_all(src, dst, lambda i: " " + payloads[i % len(payloads)], onto)


def _plant_all(src: Path, dst: Path, suffix: Callable[[int], str], onto: Ontology) -> int:
    shutil.copytree(src, dst)
    n = 0
    for p in sorted(dst.glob("*/*.csv")):
        table = f"{p.parent.name}/{p.stem}"
        with p.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f, strict=True))
            fields = list(rows[0]) if rows else []
        cols = [c for c in fields if onto.column(table, c).type == "text"]
        for r in rows:
            for c in cols:
                if r[c]:
                    r[c] = _plant(r[c], suffix(n))
                    n += 1
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fields)
            w.writeheader()
            w.writerows(rows)
    return n
