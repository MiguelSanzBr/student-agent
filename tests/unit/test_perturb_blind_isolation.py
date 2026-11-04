"""O conjunto cego foi escrito sem acesso ao guard: o isolamento é verificado por AST."""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
BLIND = ROOT / "evals/guard/perturb_blind.py"
ALLOWED_ORACLE = {"ideal_answer", "Raw", "enrollments"}  # leitores do oráculo; nada do guard


def imports(path: Path) -> list[tuple[str, set[str]]]:
    out = []
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.ImportFrom):
            out.append((n.module or "", {a.name for a in n.names}))
        elif isinstance(n, ast.Import):
            out += [(a.name, set()) for a in n.names]
    return out


def test_perturb_blind_imports_only_oracle_readers():
    for mod, names in imports(BLIND):
        top = mod.split(".")[0]
        assert top not in {"app", "tests", "core", "etl"}, mod
        assert not mod.startswith("evals.guard"), mod
        if mod == "evals.oracle":
            assert names <= ALLOWED_ORACLE, names
        else:
            assert top != "evals", mod
    assert any(m == "evals.oracle" and "ideal_answer" in n for m, n in imports(BLIND))


def test_guard_tests_do_not_import_perturb_blind():
    for p in (ROOT / "tests").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        if p.name in {"test_perturb_blind.py", Path(__file__).name}:
            continue  # testa o próprio conjunto cego, não o guard
        if "app.guards" in src or "evidence_guard" in p.name or "guard" in p.name:
            assert "perturb_blind" not in src, p
