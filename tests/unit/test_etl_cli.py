from pathlib import Path

import pytest

from etl.__main__ import main


def test_missing_golden_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="golden.json ausente"):
        main(["load", "--golden", str(tmp_path / "nao-existe.json")])


def test_command_is_optional_and_validated() -> None:
    with pytest.raises(SystemExit):
        main(["bogus"])
