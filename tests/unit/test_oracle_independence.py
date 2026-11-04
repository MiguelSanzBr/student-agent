import ast
from pathlib import Path

FORBIDDEN = {"core", "etl", "app"}


def test_oracle_imports_only_stdlib() -> None:
    tree = ast.parse(Path("evals/oracle.py").read_text())
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "import relativo"
            mods.append(node.module or "")
    assert mods
    assert not [m for m in mods if m.split(".")[0] in FORBIDDEN]
    stdlib = __import__("sys").stdlib_module_names
    assert not [m for m in mods if m.split(".")[0] not in stdlib]
