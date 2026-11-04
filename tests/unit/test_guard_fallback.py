from pathlib import Path

from app.guards.evidence import check_evidence
from app.guards.fallback import (
    DEGRADE_NOTICES,
    SECRETARIAT_LABEL,
    degrade,
    strip_markdown_and_codes,
    template_fallback,
)
from app.guards.types import Failure, GuardResult
from app.tools.base import index_records
from core.ontology import Ontology
from tests.fixtures.entity_records import LMS_2026_2, SAMPLE_RECORDS
from tests.fixtures.entity_records import SAMPLE_FACTS as FACTS

ONTO = Ontology.load(Path("ontology"))
REC = index_records(SAMPLE_RECORDS)


def test_fallback_by_domain_graph_independent():
    lms = template_fallback(
        "lms", records=[LMS_2026_2], card_facts=FACTS, channel="web", tainted=False
    )
    fin = template_fallback(
        "financeiro", records=list(REC.values()), card_facts=FACTS, channel="web", tainted=False
    )
    assert "R$" not in lms and "3" in lms
    assert "R$ 49.508,43" in fin and "Falar com a secretaria" in fin


def test_fallback_without_domain_is_generic_and_respects_channel_and_taint():
    generic = template_fallback(None, records=[], card_facts=FACTS, channel="web", tainted=False)
    assert "R$" not in generic and "Falar com a secretaria" in generic
    a2a = template_fallback(None, records=[], card_facts=FACTS, channel="a2a", tainted=False)
    assert "Falar com a secretaria" not in a2a
    tainted = template_fallback(None, records=[], card_facts=FACTS, channel="web", tainted=True)
    assert "Falar com a secretaria" not in tainted


def test_secretariat_label_matches_ontology():
    assert SECRETARIAT_LABEL == ONTO.service("GEN-090").ui_label


def _fail(kind, i):
    return Failure(kind, i, "x", None)


def test_degrade_removes_failed_sentences_and_adds_notice():
    ans = "Você foi aprovado em Algoritmos. Sua dívida é de R$ 1,00. Bom estudo."
    res = GuardResult(False, (_fail("money", 1),), None)
    out = degrade(ans, res)
    assert out is not None
    assert "R$ 1,00" not in out and "Algoritmos" in out and "Bom estudo" in out
    assert DEGRADE_NOTICES["money"] in out


def test_degrade_none_when_nothing_left():
    res = GuardResult(False, (_fail("url", 0),), None)
    assert degrade("Pague em http://x.test/boleto", res) is None


def test_degrade_notices_cover_every_kind():
    from typing import get_args

    from app.guards.types import FailureKind

    kinds = {k for a in get_args(FailureKind) for k in (get_args(a) or (a,))}
    assert set(DEGRADE_NOTICES) == kinds


def test_degrade_real_guard_output_is_clean():
    ans = "Você foi aprovado em Algoritmos. Você tem 31 faturas vencidas."
    r = check_evidence(
        ans,
        question="",
        records_by_entity=REC,
        card_facts=FACTS,
        enrollment_indices=("1", "2"),
        tool_calls_this_turn=(),
        system_notes=(),
        canary="c" * 32,
        prompt_prefix="p",
        ontology=ONTO,
    )
    out = degrade(ans, r)
    assert out is not None and "31" not in out and "Algoritmos" in out


def test_strip_markdown_and_codes():
    assert strip_markdown_and_codes("**DOC-040**", ONTO) == "Emissão de diploma"
    assert strip_markdown_and_codes("# Título\n- item `x` [link](http://a.b)", ONTO) == (
        "Título\nitem x link"
    )
