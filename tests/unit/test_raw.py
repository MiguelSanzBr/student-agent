import zipfile
from pathlib import Path

import pytest

from etl.raw import read_raw

ROOT = Path(__file__).parents[2]
MINI = ROOT / "tests/fixtures/mini"
ZIP = ROOT / "data/dados.zip"
needs_data = pytest.mark.skipif(not ZIP.exists(), reason="data/dados.zip ausente (CI sem dados)")


def test_read_raw_zip_and_dir_same_content(tmp_path: Path) -> None:
    z = tmp_path / "mini.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in sorted(MINI.glob("*/*.csv")):
            zf.write(p, f"{p.parent.name}/{p.name}")
    from_zip, from_dir = read_raw(z), read_raw(MINI)
    assert len(from_zip) == len(from_dir) == 47
    assert dict(from_zip) == dict(from_dir)


@needs_data
def test_read_raw_47_zip_and_dir(tmp_path: Path) -> None:
    raw = read_raw(ZIP)
    assert len(raw) == 47
    with zipfile.ZipFile(ZIP) as zf:
        zf.extractall(tmp_path)
    assert dict(read_raw(tmp_path)) == dict(raw)


@needs_data
def test_read_raw_utf8_headers() -> None:
    raw = read_raw(ZIP)
    assert sum(len(rows[0]) for rows in raw.values()) == 401
    assert all(not k.startswith("﻿") for rows in raw.values() for k in rows[0])
