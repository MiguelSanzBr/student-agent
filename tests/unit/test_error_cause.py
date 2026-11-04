from evals.golden.constants import REFUSAL_PREFIX
from evals.golden.error_cause import attribute_cause
from evals.golden.score import Check, DatasetItem, ScoreResult
from evals.stats import TurnTrace

FAIL = ScoreResult(False, ("x",))


def item(**kw):
    c = Check("q", expected_tools=("get_status",), required=("ativa",), **kw)
    return DatasetItem("i1", "t1", "cat", "dev", (c,))


def trace(**kw):
    out = {"get_status": "ATIVA"}
    base = dict(answer="A matrícula está trancada.", tools_called=("get_status",),
                tool_outputs=out, oracle_outputs=out)  # fmt: skip
    return TurnTrace(**{**base, **kw})


def cause(it=None, **kw):
    return attribute_cause(it or item(), trace(**kw), FAIL)


def test_error_cause_rules():
    assert cause(tools_called=()) == "roteamento"
    assert cause(tool_outputs={"get_status": "TRANCADA"}) == "dados"
    assert cause(pre_guard_answer="A matrícula está ativa.", answer="Sem dados.") \
        == "guard_bloqueou_certa"  # fmt: skip
    assert cause(answer=f"{REFUSAL_PREFIX} Outro assunto.") == "recusa_indevida"
    assert cause() == "redacao"
    assert cause(gabarito_marked=True, tools_called=()) == "gabarito"


def test_refusal_expected_is_not_unduly():
    it = DatasetItem("i", "t", "c", "dev", (Check("q", expect_refusal=True),))
    t = TurnTrace(answer=f"{REFUSAL_PREFIX} x")
    assert attribute_cause(it, t, FAIL) == "redacao"


def test_dados_from_tool_values_and_guard_rule_needs_different_text():
    # fato exigido ("ativa") ausente dos valores da ferramenta esperada que foi chamada
    assert cause(tool_values={"get_status": ["TRANCADA"]}, tool_outputs={}, oracle_outputs={}) \
        == "dados"  # fmt: skip
    assert cause(tool_values={"get_status": ["ativa"]}, tool_outputs={}, oracle_outputs={}) \
        == "redacao"  # fmt: skip
    # rascunho pré-guard igual ao final: o guard não alterou nada, não é "bloqueou certa"
    same = "A matrícula está ativa."
    assert cause(pre_guard_answer=same, answer=same) == "redacao"
