import pytest

from evals.golden.judge import build_prompt, judge
from evals.golden.score import Check, DatasetItem

ITEM = DatasetItem("i", "t", "c", "dev", (Check("Qual meu status?", required=("ativa",)),))


def test_judge_binary_with_fake_llm():
    seen = []

    def fake(prompt):
        seen.append(prompt)
        return "SIM" if "ativa" in prompt.split("Resposta do agente:")[1] else "Não."

    assert judge(ITEM, "Está ativa.", llm=fake) is True
    assert judge(ITEM, "Está trancada.", llm=fake) is False
    assert "Fatos exigidos: ativa" in seen[0] and "Qual meu status?" in seen[0]
    assert build_prompt(ITEM, "x").endswith("x\n")


def test_judge_rejects_garbage():
    with pytest.raises(ValueError):
        judge(ITEM, "x", llm=lambda _: "talvez")
