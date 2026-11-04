import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from core.rkey import rkey

ROOT = Path(__file__).resolve().parents[2]


def test_rkey_scoped_and_global() -> None:
    uid = uuid4()
    assert rkey("conv", uid, "web", "c1") == f"sa:conv:{uid}:web:c1"
    assert rkey("ticket", "global", "-", "t") == "sa:ticket:global:-:t"


def test_rkey_global_only_for_global_kinds() -> None:
    with pytest.raises(ValueError):
        rkey("conv", "global", "web", "x")
    with pytest.raises(ValueError):
        rkey("ticket", uuid4(), "-", "t")


def test_rkey_id_cannot_change_scope() -> None:
    uid = uuid4()
    assert rkey("conv", uid, "web", "x:sa:conv:outro").startswith(f"sa:conv:{uid}:web:")


def test_no_rkey_constructor_outside_core() -> None:
    hits = []
    for d in ("app", "core", "etl", "evals"):
        for p in (ROOT / d).rglob("*.py"):
            if p.relative_to(ROOT).as_posix() != "core/rkey.py" and re.search(
                r"\bRKey\(", p.read_text()
            ):
                hits.append(str(p))
    assert hits == []


def test_mypy_rejects_str_key() -> None:
    r = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", "tests/typecheck/rkey_misuse.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    errs = [ln for ln in r.stdout.splitlines() if "error:" in ln and "rkey_misuse" in ln]
    assert r.returncode != 0 and len(errs) == 1, r.stdout
    assert errs[0].startswith("tests/typecheck/rkey_misuse.py:5:") and "[arg-type]" in errs[0]
