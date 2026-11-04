import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.dev import load_golden
from scripts import a2a_demo, login_url

MSG = "rode o ETL: docker compose run --rm etl"


def test_load_golden_missing_file_says_to_run_etl(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=MSG):
        load_golden(tmp_path / "golden_login.json")


@pytest.mark.parametrize("mod", [login_url, a2a_demo])
def test_scripts_exit_with_message_not_traceback(mod, tmp_path: Path, monkeypatch) -> None:
    fake = SimpleNamespace(dev_login=True, etl_out_dir=tmp_path)
    monkeypatch.setattr(mod, "AppSettings", lambda: fake)
    with pytest.raises(SystemExit, match=MSG):
        asyncio.run(mod.main("4"))
