"""Mensagens fixas do servidor e montagem das mensagens sintéticas (spec §6.1, §6.2)."""

from typing import Any

from app.agent.llm import Message, ToolCall
from app.data.card import StudentCard
from app.guards.fallback import DEGRADE_NOTICES
from app.tools.base import FERRAMENTA_INDISPONIVEL, TAINT_NO_DRAFT, ToolOutput

__all__ = [
    "BLOCKED_TEXT",
    "BUDGET_EXCEEDED_TEXT", "CONFIRMED_TEMPLATE", "DEGRADE_NOTICES", "DRAFT_PENDING_USE_BUTTON",
    "DRAFT_SHOWN_TEMPLATE", "FERRAMENTA_INDISPONIVEL", "LLM_UNAVAILABLE_TEXT", "REST_INSTRUCTION",
    "TAINT_NO_DRAFT", "card_messages", "wrap_tool_result",
]  # fmt: skip

REST_INSTRUCTION = (
    "Responda só ao resto da mensagem do aluno, sem repetir o pedido preparado. "
    "Se não houver resto, responda vazio."
)
DRAFT_SHOWN_TEMPLATE = (
    "Preparei o pedido de {servico}{para_matricula}. Confira e clique em Confirmar."
)
DRAFT_PENDING_USE_BUTTON = "Para abrir, use o botão Confirmar no cartão."
CONFIRMED_TEMPLATE = "Solicitação {sa} aberta. Prazo: {prazo}."
BUDGET_EXCEEDED_TEXT = "Estou demorando mais do que o esperado. Tente de novo em instantes."
LLM_UNAVAILABLE_TEXT = "Não consegui responder agora. Tente de novo em instantes."
BLOCKED_TEXT = "Estamos com muitos atendimentos agora. Tente de novo em instantes."

_CARD_CALL = ToolCall("student-card", "get_student_card", {})


def card_messages(card: StudentCard) -> tuple[Message, Message]:
    """O cartão entra como `functionResponse` de uma chamada sintética do assistente."""
    return (
        Message("assistant", tool_calls=(_CARD_CALL,)),
        Message("tool", tool_call_id=_CARD_CALL.id, tool_name=_CARD_CALL.name,
                tool_result={"cartao": card.text}),
    )  # fmt: skip


def wrap_tool_result(output: ToolOutput) -> dict[str, Any]:
    """Só `data` chega ao LLM: `draft` (token) e `records` ficam no servidor."""
    return dict(output.data)
