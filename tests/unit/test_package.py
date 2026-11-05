import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "package.sh"
# o zip de entrega (git archive) não traz .git: o empacotamento só se testa no repositório
pytestmark = pytest.mark.skipif(not (ROOT / ".git").exists(), reason="sem .git (pacote de entrega)")
FORBIDDEN_PREFIX = (
    ".git/",
    "data/",
    ".lanes/",
    ".worktrees/",
    ".superpowers/",
    "evals/holdout/data/",
)


def clone(tmp_path: Path, branch: str = "main") -> Path:
    """Cópia limpa do HEAD numa branch `branch` (o script exige árvore limpa e main = HEAD)."""
    repo = tmp_path / "repo"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip
    subprocess.run(["git", "clone", "-q", "--no-checkout", str(ROOT), str(repo)], check=True)
    subprocess.run(["git", "checkout", "-q", "-B", branch, head], cwd=repo, check=True)
    if branch != "main":  # main fica no HEAD e a branch anda um commit vazio: não exige histórico (CI clona raso)
        subprocess.run(["git", "branch", "-f", "main", head], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                        "-m", "outra"], cwd=repo, check=True)  # fmt: skip
    return repo


def build(tmp_path: Path, mode: str) -> tuple[list[str], Path]:
    repo = clone(tmp_path)
    r = subprocess.run(
        ["bash", str(repo / "scripts" / "package.sh")],
        cwd=repo,
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
        name = Path(n).name
        assert name != "dados.zip", n
        assert not name.startswith(".env") or name == ".env.example", n
        assert not (
            n.startswith("evals/holdout/")
            and n.endswith((".yaml", ".yml", ".json.dec"))
            and "manifest" not in n
        ), n
        assert not n.endswith((".pem", ".key")), n
        assert "/__pycache__/" not in n
    if mode == "b":
        assert not any(n.startswith("docs/superpowers/") for n in names)
        assert not {"AGENTS.md", "CLAUDE.md"} & set(names)
        assert not (tmp_path / "student-agent.bundle").exists()
    else:
        heads = subprocess.run(
            ["git", "bundle", "list-heads", str(tmp_path / "student-agent.bundle")],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        assert heads[1::2] == ["refs/heads/main"] or set(heads[1::2]) == {"HEAD", "refs/heads/main"}


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


def _run(repo: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(repo / "scripts" / "package.sh")], cwd=repo, capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "MODE": "b", "OUT": str(out)},
    )  # fmt: skip


def test_package_refuses_dirty_tree(tmp_path: Path) -> None:
    repo = clone(tmp_path)
    (repo / "README.md").write_text("sujo\n")
    r = _run(repo, tmp_path / "out")
    assert r.returncode != 0 and "árvore" in r.stderr
    assert not (tmp_path / "out" / "student-agent.zip").exists()


def test_package_refuses_head_not_main(tmp_path: Path) -> None:
    repo = clone(tmp_path, branch="outra")
    r = _run(repo, tmp_path / "out")
    assert r.returncode != 0 and "main" in r.stderr
    assert not (tmp_path / "out" / "student-agent.zip").exists()
