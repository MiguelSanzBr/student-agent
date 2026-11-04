import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "package.sh"
FORBIDDEN_PREFIX = (
    ".git/",
    "data/",
    ".lanes/",
    ".worktrees/",
    ".superpowers/",
    "evals/holdout/data/",
)
FORBIDDEN_NAME = (".env", "dados.zip")


def build(tmp_path: Path, mode: str) -> tuple[list[str], Path]:
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "MODE": mode, "OUT": str(tmp_path)},
    )
    assert r.returncode == 0, r.stderr
    with zipfile.ZipFile(tmp_path / "student-agent.zip") as z:
        return [n.removeprefix("student-agent/") for n in z.namelist()], tmp_path


@pytest.mark.parametrize("mode", ["a", "b"])
def test_package_excludes(tmp_path: Path, mode: str) -> None:
    names, _ = build(tmp_path, mode)
    assert names
    for n in names:
        assert not n.startswith(FORBIDDEN_PREFIX), n
        assert Path(n).name not in FORBIDDEN_NAME, n
        assert not (
            n.startswith("evals/holdout/")
            and n.endswith((".yaml", ".yml", ".json.dec"))
            and "manifest" not in n
        ), n
        assert not n.endswith((".pem", ".key")), n
        assert "/__pycache__/" not in n
    if mode == "b":
        assert not any(n.startswith("docs/superpowers/") for n in names)
        assert not (tmp_path / "student-agent.bundle").exists()
    else:
        assert (tmp_path / "student-agent.bundle").exists()


def test_package_has_entrypoints(tmp_path: Path) -> None:
    names, _ = build(tmp_path, "b")
    for f in ("docker-compose.yml", "Makefile", ".env.example", "README.md"):
        assert f in names


def test_package_requires_mode(tmp_path: Path) -> None:
    r = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "OUT": str(tmp_path)},
    )
    assert r.returncode != 0 and "MODE=a" in r.stderr and "MODE=b" in r.stderr
