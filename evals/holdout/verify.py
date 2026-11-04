"""Confere o texto claro do holdout (decifrado pelo autor) contra o `plain_sha256` do manifesto."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "manifest.json"


def verify_plain(path: Path, sealed: str, manifest: Path | None = None) -> None:
    """SystemExit se o SHA-256 de `path` divergir do de `sealed` (ex.: o .age que o originou)."""
    want = json.loads((manifest or MANIFEST).read_text()).get(sealed, {}).get("plain_sha256")
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if not want or got != want:
        raise SystemExit(f"{path}: SHA-256 do texto claro não confere com o manifesto ({sealed})")
