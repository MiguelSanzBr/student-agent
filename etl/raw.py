"""Leitura dos CSV do case (zip ou diretório) como linhas de strings, sem interpretar nada."""

import csv
import io
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

RawData = Mapping[str, Sequence[Mapping[str, str]]]


def _rows(text: io.TextIOBase) -> list[dict[str, str]]:
    return list(csv.DictReader(text, strict=True))


def read_raw(source: Path) -> RawData:
    """Chave `"<sistema>/<tabela>"`; aceita `dados.zip` ou o diretório já extraído."""
    out: dict[str, list[dict[str, str]]] = {}
    if source.is_dir():
        for p in sorted(source.glob("*/*.csv")):
            with p.open(encoding="utf-8", newline="") as f:
                out[f"{p.parent.name}/{p.stem}"] = _rows(f)
        return out
    with zipfile.ZipFile(source) as z:
        for name in sorted(z.namelist()):
            parts = name.split("/")
            if len(parts) == 2 and parts[1].endswith(".csv"):
                with io.TextIOWrapper(z.open(name), encoding="utf-8", newline="") as f:
                    out[f"{parts[0]}/{parts[1][:-4]}"] = _rows(f)
    return out
