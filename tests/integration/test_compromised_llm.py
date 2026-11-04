import json
import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from evals import oracle as o
from tests.adversarial import (
    AdversarialLLM,
    Harness,
    calls_blob,
    foreign_identifiers,
    fuzz_strings,
    leaks,
)
from tests.integration.conftest import MINI

RAW = o.load_csv(MINI)
G = {c["case"]: c for c in json.loads((MINI.parent / "golden.json").read_text())["cases"]}


def _own(case: int) -> list[o.Enr]:
    return [(G[case]["brand"], G[case]["ra"]), *(tuple(e) for e in G[case]["other_enrollments"])]  # type: ignore[misc]


def test_compromised_llm_never_sees_other_person(harness: Harness) -> None:
    llm, total = AdversarialLLM(11), 0
    for case in (1, 2, 3, 4, 9):
        calls = llm.attack(harness, case, fuzz_strings(RAW, llm.rng))
        total += len(calls)
        card = harness.card(case)
        blob = calls_blob(calls) + card.text + json.dumps(dict(card.facts))
        assert leaks(blob, foreign_identifiers(RAW, _own(case))) == [], case
        # controle positivo: o próprio dono continua vendo seus dados
        assert "R$" in blob
    assert total >= 500
    assert leaks(
        "ra " + sorted(foreign_identifiers(RAW, _own(1)))[0] + ".",
        foreign_identifiers(RAW, _own(1)),
    )


def test_fuzzed_args(harness: Harness) -> None:
    ctx, names = harness.ctx(4), sorted(harness.tools)
    foreign = foreign_identifiers(RAW, _own(4))
    fz = st.one_of(
        st.text(alphabet=string.printable + "áéç⟦", max_size=40), st.integers(), st.none(),
        st.sampled_from(sorted(foreign)[:50]), st.lists(st.text(max_size=5), max_size=3),
    )  # fmt: skip
    args = st.dictionaries(
        st.sampled_from(["enrollment", "status", "period", "term", "result", "subject",
                         "numero", "service_code", "description", "ra", "cpf", "person_key"]),
        fz, max_size=4,
    )  # fmt: skip

    @settings(
        max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(st.sampled_from(names), args)
    def check(name: str, a: dict) -> None:  # type: ignore[type-arg]
        c = harness.call(ctx, name, a)  # qualquer exceção que não seja ToolError é 500
        assert leaks(calls_blob([c]), foreign) == []

    check()
