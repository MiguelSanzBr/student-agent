from pathlib import Path

import yaml

from evals import oracle as o
from tests.adversarial import AdversarialLLM, Call, Harness, fuzz_strings
from tests.integration.conftest import MINI

DECLARED = {
    n: set(v["outputs"])
    for f in (Path(__file__).resolve().parents[2] / "ontology" / "tools").glob("*.yaml")
    for n, v in yaml.safe_load(f.read_text())["tools"].items()
}


def _keys(x: object) -> set[str]:
    if isinstance(x, dict):
        return set(x) | set().union(*(_keys(v) for v in x.values()))
    if isinstance(x, (list, tuple)):
        return set().union(*(_keys(v) for v in x))
    return set()


def _undeclared(c: Call) -> set[str]:
    if c.out is None:
        return set()
    got = _keys(dict(c.out.data)) | {k for r in c.out.records for k in r.fields}
    return got - DECLARED[c.tool]


def test_tool_outputs_within_declared_fields(harness: Harness) -> None:
    raw = o.load_csv(MINI)
    llm = AdversarialLLM(7)
    bad: dict[str, set[str]] = {}
    for case in (1, 3, 4, 9):
        for c in llm.attack(harness, case, fuzz_strings(raw, llm.rng)):
            if u := _undeclared(c):
                bad.setdefault(c.tool, set()).update(u)
    assert not bad, {k: sorted(v) for k, v in bad.items()}
