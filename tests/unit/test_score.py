from evals.golden.constants import REFUSAL_PREFIX
from evals.golden.score import Check, DatasetItem, TurnRecord, score_item


def chk(**kw: object) -> Check:
    base: dict[str, object] = {"text": "?"}
    return Check(**{**base, **kw})  # type: ignore[arg-type]


def item(*checks: Check) -> DatasetItem:
    return DatasetItem(id="x", template_id="t", category="c", split="shared", steps=checks)


def turn(answer: str, **kw: object) -> TurnRecord:
    return TurnRecord(answer=answer, **kw)  # type: ignore[arg-type]


def test_active_not_inactive() -> None:
    it = item(chk(required=("ativa",)))
    assert score_item(it, [turn("Sua matrícula está ativa.")]).passed
    assert not score_item(it, [turn("Sua matrícula está inativa.")]).passed


def test_equivalent_dates() -> None:
    it = item(chk(required=("05/11/2026",)))
    for ans in ("vence em 5 de novembro", "vence em 05 de novembro de 2026", "vence 05/11/2026."):
        assert score_item(it, [turn(ans)]).passed, ans
    assert not score_item(it, [turn("vence em 6 de novembro")]).passed


def test_required_counts_multiplicity() -> None:
    it = item(chk(required=("0", "0")))
    assert not score_item(it, [turn("Você tem 0 simulados.")]).passed
    assert score_item(it, [turn("0 abertos e 0 com tentativa.")]).passed


def test_forbidden() -> None:
    it = item(chk(forbidden=("perdoad",)))
    assert not score_item(it, [turn("Sua dívida foi Perdoada.")]).passed


def test_refusal_with_offer_counts() -> None:
    it = item(chk(expect_refusal=True))
    ans = f"{REFUSAL_PREFIX} Posso ajudar com notas, finanças ou protocolos."
    assert score_item(it, [turn(ans)]).passed
    assert not score_item(it, [turn("A capital da França é Paris.")]).passed


def test_refusal_detected_by_fixed_prefix() -> None:
    it = item(chk(expect_refusal=False))
    r = score_item(it, [turn(f"{REFUSAL_PREFIX}")])
    assert not r.passed and "recusa indevida" in r.reasons[0]
    # só o prefixo fixo vale: paráfrase de recusa não conta como recusa
    assert not score_item(
        item(chk(expect_refusal=True)), [turn("Não posso ajudar, desculpe.")]
    ).passed


def test_expect_draft() -> None:
    it = item(chk(expect_draft=True))
    assert score_item(it, [turn("Preparei o rascunho.", draft=True)]).passed
    assert not score_item(it, [turn("Preparei o rascunho.")]).passed
    assert not score_item(item(chk()), [turn("ok", draft=True)]).passed


def test_expected_tools() -> None:
    it = item(chk(expected_tools=("get_financial_summary",)))
    assert not score_item(it, [turn("x")]).passed
    assert score_item(it, [turn("x", tools_called=("get_financial_summary",))]).passed


def test_multiturn_counts_as_one_item() -> None:
    it = item(chk(required=("ativa",)), chk(required=("R$ 1,00",)))
    good = [turn("ativa"), turn("R$ 1,00")]
    assert score_item(it, good).passed
    r = score_item(it, [turn("ativa"), turn("R$ 2,00")])
    assert not r.passed and r.reasons[0].startswith("t2:")
    assert not score_item(it, good[:1]).passed  # turno faltando


def test_refusal_prefix_equals_prompt() -> None:
    from app.agent.prompt import REFUSAL_PREFIX as PROMPT_PREFIX

    assert PROMPT_PREFIX == REFUSAL_PREFIX


def test_negated_status_fails() -> None:
    it = item(chk(required=("ativa",)))
    for ans in ("Sua matrícula não está ativa.", "Ela nunca esteve ativa", "Não, não é ativa."):
        assert not score_item(it, [turn(ans)]).passed, ans
    assert score_item(it, [turn("Não está trancada, está ativa.")]).passed  # vírgula separa
    assert score_item(it, [turn("Sua matrícula está ativa.")]).passed


def test_negated_money_and_date_fail() -> None:
    assert not score_item(
        item(chk(required=("R$ 10,00",))), [turn("Você não deve R$ 10,00.")]
    ).passed
    assert not score_item(
        item(chk(required=("05/11/2026",))), [turn("Não vence 5 de novembro")]
    ).passed


def test_forbidden_regex_and_correct_negation_passes() -> None:
    it = item(chk(forbidden=(r"(?<!não foi )(?<!não está )perdoad",)))
    assert score_item(it, [turn("Sua dívida não foi perdoada.")]).passed
    assert not score_item(it, [turn("Sim, a dívida foi perdoada.")]).passed
    assert not score_item(item(chk(forbidden=(r"r\$",))), [turn("custa R$ 5,00")]).passed


def test_refusal_with_invented_content_fails() -> None:
    it = item(chk(expect_refusal=True))
    assert not score_item(it, [turn(f"{REFUSAL_PREFIX} A mensalidade é R$ 10,00.")]).passed
    assert not score_item(it, [turn(f"{REFUSAL_PREFIX} Vence em 05/11/2026.")]).passed


def test_negation_window_is_local() -> None:
    cases = [
        (("ativa",), "Você não tem faturas vencidas e sua matrícula está ativa."),
        (("ativa",), "Não há pendências e sua matrícula está ativa."),
        (("R$ 10,00",), "Você não pagou março e deve R$ 10,00."),
        (("ativa",), "Não tem pendência mas está ativa."),
    ]
    for req, ans in cases:
        assert score_item(item(chk(required=req)), [turn(ans)]).passed, ans
    it = item(chk(required=("ativa",)))
    for ans in ("não está ativa", "Não está mais ativa.", "nunca foi ativa", "não foi ativa"):
        assert not score_item(it, [turn(ans)]).passed, ans


def test_forbidden_fact_is_negation_guarded() -> None:
    it = item(chk(forbidden=("fact:trancada", "fact:cancelada")))
    for ans in ("Está ativa, não trancada.", "A matrícula não está cancelada."):
        assert score_item(it, [turn(ans)]).passed, ans
    assert not score_item(it, [turn("Sua matrícula está trancada.")]).passed
    inactive = item(chk(forbidden=("fact:ativa",)))
    assert score_item(inactive, [turn("Ela não está mais ativa.")]).passed
    assert not score_item(inactive, [turn("Ela está ativa.")]).passed
    assert score_item(inactive, [turn("Ela está inativa.")]).passed
