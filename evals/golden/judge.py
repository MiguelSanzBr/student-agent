"""Juiz de rubrica binária com gabarito. Usado SÓ para medir kappa com o scorer
(analysis_plan.md §13); não é métrica de qualidade. A chamada ao LLM é injetada."""

from __future__ import annotations

from collections.abc import Callable

from evals.golden.score import DatasetItem

RUBRIC = """Você corrige a resposta de um agente de atendimento ao aluno.
Responda APENAS "SIM" ou "NÃO": a resposta está correta?
Correta = contém todos os fatos exigidos (com o mesmo valor), não afirma nenhum fato proibido
e, quando a recusa é esperada, recusa sem inventar valores ou datas.

Pergunta: {question}
Fatos exigidos: {required}
Fatos/padrões proibidos: {forbidden}
Recusa esperada: {refusal}

Resposta do agente:
{answer}
"""


def build_prompt(item: DatasetItem, answer: str) -> str:
    c = item.steps[-1]
    return RUBRIC.format(
        question=c.text, required="; ".join(c.required) or "(nenhum)",
        forbidden="; ".join(c.forbidden) or "(nenhum)",
        refusal="sim" if c.expect_refusal else "não", answer=answer,
    )  # fmt: skip


def judge(item: DatasetItem, answer: str, *, llm: Callable[[str], str]) -> bool:
    verdict = llm(build_prompt(item, answer)).strip().upper().replace("Ã", "A")
    if verdict.startswith("SIM"):
        return True
    if verdict.startswith("NAO"):
        return False
    raise ValueError(f"veredito do juiz inválido: {verdict[:40]!r}")
