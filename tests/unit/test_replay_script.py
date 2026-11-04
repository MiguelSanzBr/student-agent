"""Replay roteirizado: as mesmas ferramentas do modelo real, números vindos da saída delas."""

from collections.abc import Mapping
from datetime import date
from typing import Any, cast
from uuid import uuid4

import pytest

from app.agent.graph import TurnResult, run_turn
from app.agent.llm import Message
from app.agent.replay import DEFAULT_REPLAY, UNKNOWN_TEXT, ReplayLLM
from app.config import AppSettings
from app.data.card import StudentCard
from app.infra.stores.history import Conversation
from app.tools import finance, lms
from app.tools.base import EntityRecord, Tool, ToolContext, ToolOutput
from core.clock import monotonic
from core.scope import EnrollmentRef, StudentScope


def _fin(total: str, vencido: str, prox: str, valor: str) -> ToolOutput:
    d = {"total_para_quitar": total, "principal_vencido": vencido, "encargos": "R$ 0,00",
         "faturas_vencidas": 1, "a_vencer": valor, "acordos_ativos": 0, "payer_type": "ALUNO",
         "proximo_vencimento": prox, "valor_proximo_vencimento": valor,
         "dias_para_proximo_vencimento": 5}  # fmt: skip
    rec = EntityRecord(
        "financeiro",
        "1",
        ("matrícula 1",),
        {k: str(v) for k, v in d.items() if k != "dias_para_proximo_vencimento"},
    )
    return ToolOutput(d, (rec,))


def _lms(abertos: int, pendentes: int, prox: str) -> ToolOutput:
    d = {"matricula": "1", "periodo": "2026/2", "ultimo_acesso": "30/10/2026 09:00",
         "simulados_abertos": abertos, "simulados_com_tentativa_restante": abertos,
         "pendentes": pendentes, "tentativas_nao_oficiais": 0,
         "proxima_avaliacao": {"data": prox, "tipo": "prova", "fonte": "calendário"}}  # fmt: skip
    f = {"ultimo_acesso": d["ultimo_acesso"], "simulados_abertos": str(abertos),
         "simulados_com_tentativa_restante": str(abertos), "pendentes": str(pendentes),
         "tentativas_nao_oficiais": "0", "proxima_data": prox, "proxima_tipo": "prova",
         "proxima_fonte": "calendário"}  # fmt: skip
    # mesmos tipos de registro de get_lms_activity: a cobertura (alias AVA/LMS) e uma prova aberta
    prova = EntityRecord("prova", "Genética simulado 05/09/2026", ("Genética", "simulado"),
                         {"situacao": "aberta", "janela_inicio": "05/09/2026",
                          "janela_fim": "05/12/2026", "tentativas": "1",
                          "tentativas_permitidas": "5", "vale_nota": "não"})  # fmt: skip
    cob = EntityRecord("cobertura", "lms", ("AVA", "LMS"), {"desde": "01/07/2026"})
    return ToolOutput(d, (EntityRecord("lms", "2026/2", ("2026/2",), f), cob, prova))


GOLDEN = _fin("R$ 49.508,43", "R$ 40.000,00", "10/11/2026", "R$ 1.234,56")
OTHER = _fin("R$ 777,10", "R$ 300,00", "12/11/2026", "R$ 477,10")


async def _turn(text: str, out: ToolOutput, tool: Tool | None = None,
                llm: ReplayLLM | None = None, n_enr: int = 1) -> TurnResult:  # fmt: skip
    async def h(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        return out

    t = tool or Tool(finance.SUMMARY_SPEC, h, "financeiro")
    t = Tool(t.spec, h, t.domain)
    enrs = tuple(
        EnrollmentRef(
            str(i),
            uuid4(),
            f"Curso {i}",
            "UniSaoPaulo",
            "UniSaoPaulo",
            "presencial",
            "ACTIVE",
            i == 1,
        )
        for i in range(1, n_enr + 1)
    )
    scope, st = StudentScope(uuid4(), enrs, ("UniSaoPaulo",)), AppSettings()

    def factory(rnd: int, tainted: bool) -> ToolContext:
        return ToolContext(scope, st, cast(Any, "web"), "conv", tainted, rnd, True,
                           date(2026, 10, 31), cast(Any, None), cast(Any, None))  # fmt: skip

    return await run_turn(
        llm=llm or ReplayLLM(DEFAULT_REPLAY), settings=st, scope=scope,
        card=StudentCard("Cartão do aluno: matrícula 1.", {}),
        conversation=Conversation((), False, False, False), text=text, channel="web",
        ctx_factory=factory, deadline=monotonic() + 30, writer=lambda _: None, model="m",
        tools=(t,),
    )  # fmt: skip


@pytest.mark.parametrize("q", ["Quanto eu devo?", "quanto falta para quitar?"])
async def test_financial_script_calls_tool_and_passes_guard(app_secrets: object, q: str) -> None:
    r = await _turn(q, GOLDEN)
    assert [c.name for c in r.tool_calls] == ["get_financial_summary"]
    assert r.guard == "pass" and "R$ 49.508,43" in r.answer, r.answer


async def test_other_student_gets_own_numbers(app_secrets: object) -> None:
    r = await _turn("quanto falta para quitar?", OTHER)
    assert r.guard == "pass" and "R$ 777,10" in r.answer, r.answer
    assert "49.508" not in r.answer


async def test_lms_script_calls_tool_and_passes_guard(app_secrets: object) -> None:
    t = lms.TOOLS[0]
    r = await _turn("Quais são minhas atividades no LMS?", _lms(1, 0, "20/11/2026"), t)
    assert [c.name for c in r.tool_calls] == ["get_lms_activity"]
    assert r.guard == "pass" and "20/11/2026" in r.answer, r.answer


async def test_every_suggested_question_has_an_answer() -> None:
    llm = ReplayLLM(DEFAULT_REPLAY)
    assert len(llm.suggested()) >= 4
    for q in llm.suggested():
        out = await llm.generate(model="m", system="s", messages=[Message("user", q)],
                                 tools=[], deadline=1.0)  # fmt: skip
        assert out.tool_calls or (out.text and out.text != UNKNOWN_TEXT), q


async def test_off_script_question_is_honest(app_secrets: object) -> None:
    r = await _turn("qual a capital da França?", GOLDEN)
    assert r.answer == UNKNOWN_TEXT and not r.tool_calls


async def test_multi_enrollment_summary_passes_guard(app_secrets: object) -> None:
    a, b = GOLDEN.data, OTHER.data
    rows = [{"matrícula": "1", **a}, {"matrícula": "2", **b}]
    out = ToolOutput({"matriculas": rows, "total_geral": "R$ 50.285,53"},
                     (*GOLDEN.records, EntityRecord("financeiro", "2", ("matrícula 2",),
                                                    OTHER.records[0].fields)))  # fmt: skip
    r = await _turn("Quanto eu devo?", out, n_enr=2)
    assert r.guard == "pass", r.answer
    assert "Matrícula 2: o total para quitar é R$ 777,10." in r.answer, r.answer


async def test_next_due_script_uses_tool_values(app_secrets: object) -> None:
    r = await _turn("Qual é o meu próximo vencimento?", OTHER)
    assert r.guard == "pass" and "12/11/2026" in r.answer and "R$ 477,10" in r.answer, r.answer
