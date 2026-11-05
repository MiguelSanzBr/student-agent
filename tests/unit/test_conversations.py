from evals.conversations import SCENARIOS, Scenario, Turn, render


def test_render_shows_draft_confirmation_tools_and_status() -> None:
    sc = Scenario("Protocolo", 4, ("Preciso de uma declaração.",), "rascunho", confirm=True)
    t = Turn("Preciso de uma declaração.", "Preparei o pedido.", ["draft_protocol"], "ok",
             {"service_name_pt": "Declaração", "fee": "R$ 0,00", "due_date": "02/11/2026"},
             "Solicitação SA-2026-000001 aberta.")  # fmt: skip
    md = render([(sc, [t])], "claude-haiku-5-5")
    assert "Cartão do rascunho:** service_name_pt: Declaração; fee: R$ 0,00" in md
    assert "Depois do clique em Confirmar:** Solicitação SA-2026-000001 aberta." in md
    assert "`draft_protocol`" in md and "Status do turno: `ok`" in md


def test_scenarios_cover_the_case_requirements() -> None:
    titles = " ".join(s.title for s in SCENARIOS).lower()
    for need in ("financeiro", "cruzamento", "ava", "protocolo", "outra pessoa", "a2a"):
        assert need in titles
