import json
import re
from datetime import date

import pytest

from app.data.card import CHARS_PER_TOKEN, StudentCard
from app.tools.base import DraftCard, DraftKey
from core.format import fmt_brl, fmt_date
from evals import oracle as o
from tests.adversarial import Harness
from tests.integration.conftest import MINI

RAW = o.load_csv(MINI)
G = {c["case"]: c for c in json.loads((MINI.parent / "golden.json").read_text())["cases"]}


def _enr(case: int) -> o.Enr:
    return (G[case]["brand"], G[case]["ra"])


def test_card_golden4_vs_oracle(harness: Harness) -> None:
    f, card = o.oracle_financial(RAW, _enr(4)), harness.card(4)
    assert card.facts["1.total_para_quitar"] == fmt_brl(f.total_para_quitar) == "R$ 49.508,43"
    assert card.facts["1.proximo_vencimento"] == fmt_date(f.proximo_vencimento)  # type: ignore[arg-type]
    assert card.facts["1.valor_proximo_vencimento"] == fmt_brl(f.proximo_valor)  # type: ignore[arg-type]
    assert card.facts["1.faturas_vencidas"] == str(f.faturas_vencidas)
    assert f.pagadores == ("responsável",)
    assert card.facts["1.pagador"] == "responsável"
    for v in ("R$ 49.508,43", "responsável"):
        assert v in card.text


def test_card_golden9_diploma_line(harness: Harness) -> None:
    grad = ("UniSaoPaulo_EAD", "011843")
    links = o.oracle_link_protocols(RAW)
    want = {
        o.br_date(o._iso_date(d["Issued_At"]))
        for d in RAW["secretary/issued_document"]
        if d["Document_Type"] == "Diploma" and links[d["Protocol"]].enrollment == grad
    }
    card = harness.card(9)
    idx = next(e.index for e in harness.ctx(9).scope.enrollments if e.status == "GRADUATED")
    assert {card.facts[f"{idx}.diploma_emitido_em"]} == want
    assert f"Diploma emitido em {next(iter(want))}" in card.text
    other = next(e.index for e in harness.ctx(9).scope.enrollments if e.status != "GRADUATED")
    assert f"{other}.diploma_emitido_em" not in card.facts


@pytest.mark.parametrize("case", [1, 3, 8])
def test_card_next_assessment_and_practice(harness: Harness, case: int) -> None:
    f, card = o.oracle_lms(RAW, _enr(case)), harness.card(case)
    assert card.facts["1.simulados_abertos"] == str(f.simulados_abertos)
    if f.proxima_avaliacao:
        assert card.facts["1.proxima_avaliacao"] == f.proxima_avaliacao
        assert f"fonte: {f.fonte}" in card.text
    else:
        assert "1.proxima_avaliacao" not in card.facts


def test_card_budget(harness: Harness) -> None:
    # CHARS_PER_TOKEN é provisório (spike do Gemini não rodou); conservador para PT-BR.
    assert CHARS_PER_TOKEN == 3.5
    for case in (1, 2, 3, 4, 9):
        card = harness.card(case)
        assert len(card.text) / CHARS_PER_TOKEN <= 400, case


def test_card_has_no_person_name(harness: Harness) -> None:
    enrs, people = o.enrollments(RAW), {p["PERSON_ID"]: p for p in RAW["sag/person"]}
    for case in G:
        full = people[enrs[_enr(case)]["PERSON_ID"]]["FULL_NAME"]
        card = harness.card(case)
        hay = (card.text + json.dumps(dict(card.facts), ensure_ascii=False)).casefold()
        for part in (full, full.split()[0]):
            assert re.search(rf"\b{re.escape(part.casefold())}\b", hay) is None, (case, part)


def test_card_pending_draft_line(harness: Harness) -> None:
    ctx = harness.ctx(1)
    d = DraftCard("d1", DraftKey("DOC-001", None), "Declaração de matrícula", None,
                  "R$ 0,00", "01/11/2026", "x", "w", "c1", "1")  # fmt: skip
    pk = harness.person[1]

    async def go() -> StudentCard:
        from app.data.card import build_student_card
        from app.data.db import with_scope

        async with with_scope(harness.pool, pk, ctx.scope.brand_families) as conn:
            return await build_student_card(
                conn, ctx.scope, harness.settings, today=date(2026, 10, 31), pending=d
            )

    card = harness.run(go())
    assert "Pedido de Declaração de matrícula aguardando Confirmar" in card.text
    assert "tok" not in card.text


def test_card_open_protocol_without_prazo(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.tools import protocols
    from app.tools.base import ToolOutput

    rows = [
        {"protocolo": "SA-2026-000001", "servico": "x", "status": "aberto",
         "aberto_em": "01/10/2026", "prazo": "não informado", "solicitante": "aluno"},
        {"protocolo": "12345678901", "servico": "y", "status": "em análise",
         "aberto_em": "01/10/2026", "prazo": "05/11/2026", "solicitante": "aluno"},
        {"protocolo": "12345678902", "servico": "y", "status": "resolvido",
         "aberto_em": "01/10/2026", "prazo": "01/11/2026", "solicitante": "aluno"},
    ]  # fmt: skip

    async def fake(ctx: object, args: object) -> ToolOutput:
        return ToolOutput({"protocolos": rows})

    monkeypatch.setattr(protocols, "_list", fake)
    card = harness.card(1)
    assert card.facts["geral.protocolos_abertos"] == "2"
    assert card.facts["geral.proximo_prazo"] == "05/11/2026"
    only_blank = [rows[0]]
    rows[:] = only_blank
    card = harness.card(1)  # nenhum prazo legível: o cartão sai sem a data
    assert card.facts["geral.protocolos_abertos"] == "1"
    assert "geral.proximo_prazo" not in card.facts
