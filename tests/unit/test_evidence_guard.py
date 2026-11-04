from pathlib import Path

import pytest

from app.guards.evidence import check_evidence
from app.guards.types import GuardResult
from app.tools.base import ToolCallTrace, index_records
from core.ontology import Ontology
from tests.fixtures.entity_records import (
    ACTIVE_DEAL,
    FIN031_ORIENTATION,
    LMS_2026_2,
    SAMPLE_RECORDS,
)
from tests.fixtures.entity_records import SAMPLE_FACTS as FACTS

ONTO = Ontology.load(Path("ontology"))
PREFIX = (
    "Você é o assistente de atendimento ao aluno e responde só com dados do cartão e das "
    "ferramentas."
)
REC = index_records(SAMPLE_RECORDS)
NOTE = ("Solicitação SA-2026-000123 aberta em 31/10/2026",)


def chk(answer, *, q="", records=REC, calls=(), notes=()) -> GuardResult:  # type: ignore[no-untyped-def]
    return check_evidence(
        answer,
        question=q,
        records_by_entity=records,
        card_facts=FACTS,
        enrollment_indices=("1", "2"),
        tool_calls_this_turn=calls,
        system_notes=notes,
        canary="c" * 32,
        prompt_prefix=PREFIX,
        ontology=ONTO,
    )


def with_(*extra):  # type: ignore[no-untyped-def]
    return {**REC, **index_records(extra)}


@pytest.mark.parametrize(
    "answer",
    [
        "Você foi aprovado em Algoritmos.",
        "Sua média em Algoritmos foi 8,5.",
        "Você ficou reprovado em Cálculo I, com frequência de 80%.",
        "Sua dívida total é de R$ 49.508,43, em 32 faturas vencidas.",
        "A fatura 7067951 vence em 05/11/2026 e, para quitar, são R$ 99,12.",
        "A previsão de conclusão é 2028/1, se você cursar regularmente.",
        "Na matrícula 2 você não deve nada.",
        "Você tem bolsa de 20%.",
        "Seu acordo AG2024000128 tinha 10 parcelas e está rompido.",
        "O pedido 20260022332 foi aberto em 08/10/2026.",
        "Posso preparar a Solicitação de bolsa ou desconto para você.",
        # controles dos ajustes de léxico
        "Você pode ver no máximo 5 itens por página.",
        "Isso depende da sua matrícula; certo, posso explicar.",
        "Veja sem pressa o que preciso confirmar.",
    ],
)
def test_positive_controls_pass(answer):
    r = chk(answer)
    assert r.ok, r.failures


def test_short_yes_imports_true_claim():
    assert chk("Sim, passou.", q="passei em Algoritmos?").ok


def test_short_yes_needs_confirmation_word_not_loose_ok():
    # "ok" solto não é resposta curta afirmativa: não importa a pergunta.
    assert chk("Ok.", q="estou em dia com as mensalidades?").ok


def test_action_with_system_note():
    assert chk("Abri a solicitação SA-2026-000123.", notes=NOTE).ok


def test_tool_arg_counts_when_tool_returned_records():
    r = chk(
        "Em 2026/2 você teve 3 acessos.",
        records=with_(LMS_2026_2),
        calls=(ToolCallTrace("get_lms_activity", {"period": "2026/2"}, 1),),
    )
    assert r.ok, r.failures


def test_refusal_repeating_requested_year_passes():
    r = chk(
        "O AVA só tem dados a partir de 15/01/2023, então não há registros de 2022.",
        q="minhas notas no AVA em 2022?",
        calls=(ToolCallTrace("get_lms_activity", {"period": "2022"}, 0),),
    )
    assert r.ok, r.failures


def test_promise_backed_by_active_agreement():
    assert chk(
        "Você pode pagar em 10x pelo seu acordo ativo AG2026000340.", records=with_(ACTIVE_DEAL)
    ).ok


def test_promise_backed_by_service_orientation():
    r = chk(
        "Pela Renegociação de dívida, você pode pedir parcelamento em até 12x, conforme análise.",
        records=with_(FIN031_ORIENTATION),
    )
    assert r.ok, r.failures


def test_status_anchored_to_cited_entity():
    assert chk("Você foi aprovado em Cálculo I.").kinds() == {"status"}


def test_entity_without_record_fails():
    other = next(n for n in ONTO.subject_names_pt() if n not in ("Cálculo I", "Algoritmos"))
    assert chk(f"Você foi aprovado em {other}.").kinds() == {"entity_without_record"}


def test_debt_noun_anchored_to_total():
    assert chk("Sua dívida é de R$ 42.636,26.").kinds() == {"money"}


def test_money_not_substring():
    assert chk("Você deve R$ 42,00.").kinds() == {"money"}


def test_written_date():
    assert chk("A fatura 7067951 vence dia 5 de novembro.").ok
    assert chk("A fatura 7067951 vence dia 6 de novembro.").kinds() == {"date"}


def test_pct_and_int_need_source():
    assert chk("Sua frequência em Cálculo I foi de 90%.").kinds() == {"pct"}
    assert chk("Você tem 31 faturas vencidas.").kinds() == {"int"}


@pytest.mark.parametrize(
    "answer",
    [
        "Você está em dia.",
        "Você não tem faturas vencidas.",
        "Você não deve nada.",
        "Seu saldo é R$ 0,00.",
        "Sua dívida é zero.",
        "Você está sem pendências.",
    ],
)
def test_polarity_without_entity_needs_all_enrollments(answer):
    assert chk(answer).kinds() == {"polarity"}


def test_short_yes_imports_polarity():
    assert chk("Sim.", q="estou em dia com as mensalidades?").kinds() == {"polarity"}


def test_short_yes_imports_commitment():
    assert chk("Sim, pode.", q="posso parcelar em 10x?").kinds() == {"commitment"}


def test_period_from_question_is_not_evidence():
    assert "period" in chk("Sim, você se forma em 2027/2.", q="eu me formo em 2027/2?").kinds()


def test_tool_arg_echo_without_records_is_not_evidence():
    r = chk(
        "Em 2026/2 você teve 3 acessos.",
        calls=(ToolCallTrace("get_lms_activity", {"period": "2026/2"}, 0),),
    )
    assert "period" in r.kinds()


def test_commitment_needs_source():
    assert chk("Você pode parcelar em 10x sem juros.").kinds() == {"commitment"}


def test_strong_promise_is_commitment():
    assert chk("Garanto que será resolvido.").kinds() == {"commitment"}


def test_broken_agreement_is_not_a_source():
    assert chk("Você pode parcelar em 10x, como no acordo AG2024000128.").kinds() == {"commitment"}


@pytest.mark.parametrize(
    "answer",
    [
        "Pronto, gerei sua 2ª via.",
        "Seu pedido foi enviado.",
        "Registramos seu pedido.",
        "O sistema gerou sua 2ª via.",
        "Seu pedido está registrado.",
        "Vou abrir a solicitação agora.",
        "Vou registrar seu pedido.",
    ],
)
def test_action_by_lemma_and_future_needs_system_note(answer):
    assert chk(answer).kinds() == {"action"}


def test_student_sa_never_proves_action():
    r = chk("Abri a solicitação SA-2026-000123.", q="confirme a SA-2026-000123")
    assert "action" in r.kinds()


@pytest.mark.parametrize(
    "answer,kind",
    [
        ("Pague em http://x.test/boleto", "url"),
        ("Acesse pagamentos.exemplo.com.br para pagar.", "opaque"),
        ("Use a chave PIX 123e4567-e89b-12d3-a456-426614174000.", "opaque"),
        ("Código: U2VuaGFTZWNyZXRhMTIzNDU2Nzg5MEFCQ0RFRg==", "opaque"),
    ],
)
def test_url_and_opaque_tokens_blocked(answer, kind):
    assert kind in chk(answer).kinds()


def test_length_ceiling():
    assert chk("Você foi aprovado em Algoritmos. " * 80).kinds() == {"length"}


def test_canary_and_prompt_ngram():
    assert "canary" in chk("c" * 32).kinds()
    assert "prompt_ngram" in chk(" ".join(PREFIX.split()[:8])).kinds()


def test_guard_result_domain_from_failures():
    assert chk("Em 2026/2 você teve 9 acessos.", records=with_(LMS_2026_2)).domain == "lms"


def test_lexicon_cleanup():
    lex = ONTO.guard_lexicon()
    assert "com certeza" not in lex.commitment_terms  # fica só em confirmation_words
    assert "no máximo" not in lex.commitment_terms
    assert not set(lex.commitment_terms) & set(lex.confirmation_words)
    assert not {"ok", "certo", "isso"} & set(lex.confirmation_words)
    assert "sem" not in lex.negators


# --- fix round 1 ---------------------------------------------------------------------------
ZERO_CARD = {"1.total_para_quitar": "R$ 0,00", "1.faturas_vencidas": "0"}


def chk0(answer, *, records=None, calls=()):  # type: ignore[no-untyped-def]
    return check_evidence(
        answer,
        question="",
        records_by_entity=records or {},
        card_facts=ZERO_CARD,
        enrollment_indices=("1",),
        tool_calls_this_turn=calls,
        system_notes=(),
        canary="c" * 32,
        prompt_prefix=PREFIX,
        ontology=ONTO,
    )


@pytest.mark.parametrize(
    "answer,tool",
    [
        ("Nenhuma prova foi encontrada.", "get_lms_activity"),
        ("Não há protocolos abertos.", "list_protocols"),
        ("Você não possui acordos ativos.", "list_agreements"),
        ("Não há simulados pendentes.", "get_lms_activity"),
        ("Não há descontos vigentes.", "get_financial_summary"),
    ],
)
def test_empty_domain_answer_backed_by_zero_record_tool_call(answer, tool):
    r = chk0(answer, calls=(ToolCallTrace(tool, {}, 0),))
    assert r.ok, r.failures
    # sem chamada de ferramenta que sustente, continua bloqueado
    assert chk0(answer).kinds() == {"polarity"}


def test_empty_answer_blocked_when_tool_returned_records_of_that_kind():
    from app.tools.base import EntityRecord

    open_p = EntityRecord("protocolo", "20260022332", ("20260022332",), {"status": "aberto"})
    r = chk0(
        "Não há protocolos abertos.",
        records=index_records((open_p,)),
        calls=(ToolCallTrace("list_protocols", {}, 1),),
    )
    assert r.kinds() == {"polarity"}


@pytest.mark.parametrize(
    "answer",
    [
        "Sua média em Algoritmos foi 4,2.",
        "Sua frequência em Algoritmos foi de 80%.",
        "A fatura 7067951 vence em 08/10/2026.",
        "O protocolo 20260022332 vence em 05/11/2026.",
    ],
)
def test_numbers_anchored_to_cited_record(answer):
    assert chk(answer).ok is False


def test_cross_entity_positive_controls():
    assert chk("Sua média em Cálculo I foi 4,2.").ok
    assert chk("Sua frequência em Cálculo I foi de 80%.").ok
    assert chk("O protocolo 20260022332 foi aberto em 08/10/2026.").ok
    assert chk("A fatura 7067951 vence em 05/11/2026.").ok


def test_leak_reported_even_when_answer_too_long():
    r = chk("Você foi aprovado em Algoritmos. " * 80 + "c" * 32)
    assert {"length", "canary"} <= r.kinds()


def test_count_from_card_with_cited_record_is_evidence():
    from app.tools.base import EntityRecord

    rec = EntityRecord("protocolo", "20260022332", ("20260022332",), {"status": "aberto"})
    r = check_evidence(
        "Você tem 1 protocolos em aberto: 20260022332.",
        question="",
        records_by_entity=index_records((rec,)),
        card_facts={"1.protocolos_abertos": "1"},
        enrollment_indices=("1",),
        tool_calls_this_turn=(),
        system_notes=(),
        canary="c" * 32,
        prompt_prefix=PREFIX,
        ontology=ONTO,
    )
    assert r.ok, r.failures


# --- fix round 2 ---------------------------------------------------------------------------
def chk_with(answer, *, card, records=None, calls=(), q=""):  # type: ignore[no-untyped-def]
    return check_evidence(
        answer,
        question=q,
        records_by_entity=records or {},
        card_facts=card,
        enrollment_indices=("1",),
        tool_calls_this_turn=calls,
        system_notes=(),
        canary="c" * 32,
        prompt_prefix=PREFIX,
        ontology=ONTO,
    )


@pytest.mark.parametrize(
    "answer,tool,args",
    [
        ("Sua média em Algoritmos foi 4,2.", "get_course_results", {"min_media": "4,2"}),
        ("A fatura 7067951 vence em 08/10/2026.", "list_invoices", {"de": "08/10/2026"}),
        ("Sua frequência em Algoritmos foi de 80%.", "get_course_results", {"x": "80%"}),
        ("Você tem 77 faturas vencidas.", "list_invoices", {"n": "77"}),
        ("O protocolo 20260099 vence.", "get_protocol", {"id": "20260099"}),
    ],
)
def test_tool_args_never_launder_values(answer, tool, args):
    r = chk(answer, calls=(ToolCallTrace(tool, args, 3),))
    assert not r.ok, r.failures


def test_tool_args_still_support_period():
    r = chk(
        "Em 2026/2 você teve 3 acessos.",
        records=with_(LMS_2026_2),
        calls=(ToolCallTrace("get_lms_activity", {"period": "2026/2"}, 1),),
    )
    assert r.ok, r.failures


def test_empty_call_with_narrowing_filter_does_not_sustain_absence():
    ok = chk_with(
        "Não há protocolos abertos.",
        card={},
        calls=(ToolCallTrace("list_protocols", {"status": "aberto"}, 0),),
    )
    assert ok.ok, ok.failures
    cancelled = chk_with(
        "Não há protocolos abertos.",
        card={},
        calls=(ToolCallTrace("list_protocols", {"status": "cancelado"}, 0),),
    )
    assert cancelled.kinds() == {"polarity"}
    period = chk_with(
        "Não há protocolos abertos.",
        card={},
        calls=(ToolCallTrace("list_protocols", {"period": "2025/1"}, 0),),
    )
    assert period.kinds() == {"polarity"}


def test_nonzero_card_count_blocks_absence_even_with_empty_call():
    r = chk_with(
        "Não há protocolos abertos.",
        card={"1.protocolos_abertos": "2"},
        calls=(ToolCallTrace("list_protocols", {"status": "cancelado"}, 0),),
    )
    assert r.kinds() == {"polarity"}
    r = chk_with(
        "Não há protocolos abertos.",
        card={"1.protocolos_abertos": "2"},
        calls=(ToolCallTrace("list_protocols", {}, 0),),
    )
    assert r.kinds() == {"polarity"}


def test_unmapped_noun_has_no_absence_evidence():
    r = chk_with("Não há turmas abertas.", card={}, calls=(ToolCallTrace("whatever", {}, 0),))
    assert r.kinds() == {"polarity"}


@pytest.mark.parametrize(
    "answer",
    [
        "Dá para parcelar em 6x no cartão.",
        "Vamos conceder um desconto de 15%.",
        "É possível parcelar em 3x.",
        "Consigo parcelar em 4x.",
        "Conseguimos prorrogar o prazo por 10 dias.",
        "Fica liberado o parcelamento em 5x.",
        "Vamos dar isenção da multa.",
        "Vamos liberar desconto de 5%.",
    ],
)
def test_promise_constructions_need_a_source(answer):
    assert "commitment" in chk(answer).kinds()


def test_review_focus_5_installments():
    assert chk("Sim, pode.", q="posso parcelar em 10x?").kinds() == {"commitment"}
    assert chk("Dá para parcelar em 10x.").kinds() == {"commitment"}
    assert chk("Dá para parcelar em 12x.", records=with_(FIN031_ORIENTATION)).ok
    assert chk("É possível parcelar em 10x.", records=with_(ACTIVE_DEAL)).ok


@pytest.mark.parametrize(
    "answer",
    [
        "Sua conta está zerada.",
        "Não restou saldo a pagar.",
        "Nada a pagar.",
        "Você está quitado.",
        "Nenhum valor a pagar.",
    ],
)
def test_debt_negation_synonyms_are_financial_polarity(answer):
    assert chk(answer).kinds() == {"polarity"}
    assert chk("Na matrícula 2: " + answer).ok


def test_participle_status_is_status_not_action():
    from app.tools.base import EntityRecord

    call = (ToolCallTrace("get_lms_activity", {}, 0),)
    assert chk_with("Nenhum simulado está aberto.", card={}, calls=call).ok
    open_f = EntityRecord("fatura", "9000001", ("9000001",), {"status": "aberta"})
    paid_f = EntityRecord("fatura", "9000001", ("9000001",), {"status": "paga"})
    late_f = EntityRecord("fatura", "9000001", ("9000001",), {"status": "vencida"})
    assert chk_with("A fatura está aberta.", card={}, records=index_records((open_f,))).ok
    assert chk_with("A fatura 9000001 está aberta.", card={}, records=index_records((open_f,))).ok
    assert chk_with("A fatura está aberta.", card={}, records=index_records((paid_f,))).kinds() == {
        "status"
    }
    assert chk_with("A fatura está vencida.", card={}, records=index_records((late_f,))).ok
    assert chk_with(
        "A fatura está vencida.", card={}, records=index_records((open_f,))
    ).kinds() == {"status"}
    assert "action" in chk("Seu pedido está registrado.").kinds()


def test_leak_before_length_prompt_ngram_variant():
    long = "Você foi aprovado em Algoritmos. " * 80 + " ".join(PREFIX.split()[:8])
    assert {"length", "prompt_ngram"} <= chk(long).kinds()


def test_zero_like_rejects_empty():
    from app.guards.evidence import _zero_like

    assert not _zero_like("") and not _zero_like("  ")
    assert _zero_like("R$ 0,00") and _zero_like("0") and _zero_like("nenhum")
