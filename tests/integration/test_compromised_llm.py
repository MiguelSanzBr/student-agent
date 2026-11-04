import json
import re
import string

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.tools.protocols import NOT_FOUND
from evals import oracle as o
from tests.adversarial import (
    AdversarialLLM,
    Harness,
    calls_blob,
    foreign_identifiers,
    foreign_secrets,
    fuzz_strings,
    leaks,
)
from tests.integration.conftest import MINI

RAW = o.load_csv(MINI)
G = {c["case"]: c for c in json.loads((MINI.parent / "golden.json").read_text())["cases"]}


def _own(case: int) -> list[o.Enr]:
    return [(G[case]["brand"], G[case]["ra"]), *(tuple(e) for e in G[case]["other_enrollments"])]  # type: ignore[misc]


def _public(h: Harness, case: int) -> str:
    """O que o próprio aluno vê do catálogo (preços, calendário): não é segredo de ninguém."""
    ctx = h.ctx(case)
    kinds = ("curso", "grade", "disciplina", "calendario", "unidade", "servico", "preco")
    return calls_blob(
        [
            h.call(ctx, "get_catalog", {"kind": k, "modality": m})
            for k in kinds
            for m in ("presencial", "ead")
        ]
    )


def test_compromised_llm_never_sees_other_person(harness: Harness) -> None:
    llm, total = AdversarialLLM(11), 0
    for case in (1, 2, 3, 4, 9):
        calls = llm.attack(harness, case, fuzz_strings(RAW, llm.rng))
        total += len(calls)
        card = harness.card(case)
        blob = calls_blob(calls) + card.text + json.dumps(dict(card.facts))
        secrets = foreign_secrets(RAW, _own(case), _public(harness, case))
        assert any(s.startswith("R$") for s in secrets)
        assert leaks(blob, secrets) == [], case
        assert "R$" in blob  # o próprio dono continua vendo seus dados
    assert total >= 500


def test_positive_control_detector_flags_wrong_person(harness: Harness) -> None:
    """Rodando sob o escopo do caso 4 e tratando o caso 4 como 'outra pessoa', há acertos."""
    ctx, own1 = harness.ctx(4), _own(1)
    calls = [
        harness.call(ctx, "get_financial_summary", {}),
        harness.call(ctx, "list_invoices", {}),
        harness.call(ctx, "list_protocols", {}),
        harness.call(ctx, "get_academic_profile", {}),
    ]
    hits = leaks(calls_blob(calls), foreign_secrets(RAW, own1))
    assert any(h.startswith("R$") for h in hits), hits
    assert any(re.fullmatch(r"\d{7}", h) for h in hits), hits  # números de fatura do caso 4


def test_foreign_lookups_are_not_found(harness: Harness) -> None:
    """Todo protocolo, RA e CPF alheio devolve 'não encontrado' e nenhum registro."""
    ctx, own = harness.ctx(4), _own(4)
    foreign = foreign_identifiers(RAW, own)
    protos = sorted(x for x in foreign if re.fullmatch(r"\d{11}", x))[:40]
    assert len(protos) == 40
    for n in protos:
        c = harness.call(ctx, "get_protocol", {"numero": n})
        assert c.out is not None and dict(c.out.data) == {"mensagem": NOT_FOUND}, n
        assert c.out.records == ()
    for x in [*sorted(foreign)[:25], *(p["CPF"] for p in RAW["sag/person"][:5])]:
        for tool, key in (("list_course_results", "subject"), ("get_catalog", "query"),
                          ("list_protocols", "status"), ("list_invoices", "period")):  # fmt: skip
            extra = {"kind": "disciplina"} if tool == "get_catalog" else {}
            c = harness.call(ctx, tool, {**extra, key: x})
            if c.out:
                assert leaks(calls_blob([c]), foreign) == [], (tool, x)
                assert not [r for r in c.out.records if x in (r.id, *r.aliases)], (tool, x)


def test_fuzzed_args(harness: Harness) -> None:
    ctx, names = harness.ctx(4), sorted(harness.tools)
    foreign = foreign_secrets(RAW, _own(4), _public(harness, 4))
    fz = st.one_of(
        st.text(alphabet=string.printable + "áéç⟦", max_size=40),
        st.integers(),
        st.none(),
        st.sampled_from(sorted(foreign_identifiers(RAW, _own(4)))[:50]),
        st.lists(st.text(max_size=5), max_size=3),
    )
    args = st.dictionaries(
        st.sampled_from(
            ["enrollment", "status", "period", "term", "result", "subject", "numero",
             "service_code", "description", "ra", "cpf", "person_key"]
        ),
        fz,
        max_size=4,
    )  # fmt: skip

    @settings(
        max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
    )
    @given(st.sampled_from(names), args)
    def check(name: str, a: dict) -> None:  # type: ignore[type-arg]
        c = harness.call(ctx, name, a)  # qualquer exceção que não seja ToolError é 500
        assert leaks(calls_blob([c]), foreign) == []

    check()
