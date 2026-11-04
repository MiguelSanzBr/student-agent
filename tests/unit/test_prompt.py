from app.agent.messages import (
    CONFIRMED_TEMPLATE,
    DEGRADE_NOTICES,
    DRAFT_PENDING_USE_BUTTON,
    DRAFT_SHOWN_TEMPLATE,
    FERRAMENTA_INDISPONIVEL,
    REST_INSTRUCTION,
    TAINT_NO_DRAFT,
    card_messages,
    wrap_tool_result,
)
from app.agent.prompt import REFUSAL_PREFIX, SYSTEM_PREFIX, system_prompt
from app.config import AppSettings
from app.data.card import StudentCard
from app.tools.base import DraftCard, DraftKey, ToolOutput


def test_refusal_prefix_in_prompt() -> None:
    assert REFUSAL_PREFIX == "Não posso ajudar com isso."
    assert REFUSAL_PREFIX in SYSTEM_PREFIX


def test_system_prompt_has_canary_and_prefix(app_secrets: object) -> None:
    s = AppSettings()
    p = system_prompt(s)
    assert p.startswith(SYSTEM_PREFIX) and s.prompt_canary in p
    assert s.prompt_canary not in SYSTEM_PREFIX


def test_fixed_texts() -> None:
    assert REST_INSTRUCTION == (
        "Responda só ao resto da mensagem do aluno, sem repetir o pedido preparado. "
        "Se não houver resto, responda vazio."
    )
    assert DRAFT_SHOWN_TEMPLATE.format(servico="X", para_matricula=" para Y") == (
        "Preparei o pedido de X para Y. Confira e clique em Confirmar."
    )
    assert DRAFT_PENDING_USE_BUTTON == "Para abrir, use o botão Confirmar no cartão."
    assert CONFIRMED_TEMPLATE.format(sa="SA-2026-000001", prazo="01/01/2027") == (
        "Solicitação SA-2026-000001 aberta. Prazo: 01/01/2027."
    )
    assert TAINT_NO_DRAFT and FERRAMENTA_INDISPONIVEL and DEGRADE_NOTICES["money"]


def test_card_messages_synthetic_function_response() -> None:
    a, t = card_messages(StudentCard("Cartão X", {}))
    assert a.role == "assistant" and a.tool_calls[0].name == "get_student_card"
    assert t.role == "tool" and t.tool_call_id == a.tool_calls[0].id
    assert t.tool_name == "get_student_card" and t.tool_result == {"cartao": "Cartão X"}


def test_wrap_tool_result_only_data() -> None:
    d = DraftCard("id1", DraftKey("DOC-001", None), "Decl", None, "R$ 0,00", "01/01/2027",
                  "", "aviso", "conv", "1")  # fmt: skip
    out = wrap_tool_result(ToolOutput({"a": "b"}, draft=d, untrusted=True))
    assert out == {"a": "b"} and "TOKEN" not in str(out)
