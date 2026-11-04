"""Cifra o holdout cego com a chave pública do autor e atualiza o manifesto (por arquivo).

    uv run python -m evals.holdout.seal [plaintext] [--out evals/holdout]

O texto claro (data/holdout/templates_test_only.yaml, fora do git) só é lido aqui: nada dele
é impresso. O manifesto guarda o SHA-256 do `.age` e a contagem de templates `test_only`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pyrage
import yaml
from pyrage import x25519

ROOT = Path(__file__).resolve().parents[2]
PLAIN = ROOT / "data/holdout/templates_test_only.yaml"
OUT = Path(__file__).resolve().parent


def count_test_only(plain: bytes) -> int:
    doc = yaml.safe_load(plain)
    tpls = doc["templates"] if isinstance(doc, dict) else doc
    if not tpls or any(t.get("split") != "test_only" for t in tpls):
        raise ValueError("todo template do holdout deve ter split == test_only")
    return len(tpls)


def seal(plain: Path, pub: Path, out: Path) -> None:
    data = plain.read_bytes()
    n = count_test_only(data)
    enc = pyrage.encrypt(data, [x25519.Recipient.from_str(pub.read_text().strip())])
    name = plain.name + ".age"
    (out / name).write_bytes(enc)
    mf = out / "manifest.json"
    man = json.loads(mf.read_text()) if mf.exists() else {}
    man[name] = {"sha256": hashlib.sha256(enc).hexdigest(), "test_only": n}
    mf.write_text(json.dumps(man, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    seal(Path(sys.argv[1]) if len(sys.argv) > 1 else PLAIN, OUT / "author.pub", OUT)
    print("holdout cifrado e manifesto atualizado")
