"""Nenhum CPF do dataset do case em arquivo versionado (os dados reais ficam fora do git)."""

import csv
import io
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
ZIP = ROOT / "data/dados.zip"
_CPF = re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}|(?<!\d)\d{8,11}(?!\d)")  # com ou sem zeros à esquerda


@pytest.mark.skipif(not ZIP.exists(), reason="data/dados.zip ausente (CI sem dados)")
def test_no_dataset_cpf_in_tracked_files() -> None:
    real: set[str] = set()
    with zipfile.ZipFile(ZIP) as z:
        for n in (n for n in z.namelist() if n.endswith(".csv")):
            for r in csv.DictReader(io.TextIOWrapper(z.open(n), encoding="utf-8")):
                for k, v in r.items():
                    d = re.sub(r"\D", "", v or "")
                    if len(d) == 11 and ("cpf" in k.lower() or "taxid" in k.lower()):
                        real.add(d)
    assert len(real) > 100
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode()
    hits: dict[str, int] = {}
    for f in (f for f in out.split("\0") if f and not f.endswith(".age")):
        text = (ROOT / f).read_bytes().decode("utf-8", "ignore")
        n = sum(re.sub(r"\D", "", m[0]).zfill(11) in real for m in _CPF.finditer(text))
        if n:
            hits[f] = n
    assert not hits, f"CPF do dataset em arquivo versionado (arquivo: ocorrências): {hits}"
