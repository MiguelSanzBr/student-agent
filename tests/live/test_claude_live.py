"""Ida e volta real com a Anthropic: pytest -m live tests/live/test_claude_live.py."""

import os
from types import SimpleNamespace

import pytest

from app.agent.claude import AnthropicProvider
from app.agent.llm import Message, ToolCall
from app.agent.messages import card_messages
from app.config import AppSettings
from app.tools.base import ToolSpec
from core.clock import monotonic

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"), reason="pendente: sem ANTHROPIC_API_KEY"
    ),
]
MODEL = os.environ.get("ANTHROPIC_MODEL") or "claude-haiku-5-5"
SPEC = ToolSpec(
    "get_clima", "Clima de uma cidade.",
    {"type": "object", "properties": {"cidade": {"type": "string"}}, "required": ["cidade"],
     "additionalProperties": False},
)  # fmt: skip


async def test_roundtrip_with_card_and_thinking_resend(app_secrets: object) -> None:
    p = AnthropicProvider(AppSettings(anthropic_api_key=os.environ["ANTHROPIC_API_KEY"]))
    card = SimpleNamespace(text="Aluno: Ana, matrícula 1.")
    history = [*card_messages(card),  # type: ignore[arg-type]
               Message("user", "Qual o clima em Recife? Use a ferramenta.")]  # fmt: skip
    r1 = await p.generate(model=MODEL, system="Atendente. Use as ferramentas.", messages=history,
                          tools=[SPEC], deadline=monotonic() + 60)  # fmt: skip
    assert r1.tool_calls, r1
    call = r1.tool_calls[0]
    history += [
        Message("assistant", r1.text, r1.tool_calls, provider_meta=r1.provider_meta, model=MODEL),
        Message("tool", tool_call_id=call.id, tool_name=call.name, tool_result={"clima": "sol"}),
    ]
    r2 = await p.generate(model=MODEL, system="Atendente. Use as ferramentas.", messages=history,
                          tools=[SPEC], deadline=monotonic() + 60)  # fmt: skip
    assert r2.text and r2.finish_reason == "STOP"
    # caminho de feedback do grafo: sem ferramentas, mas com tool_use/tool_result no histórico
    history.append(Message("assistant", r2.text, provider_meta=r2.provider_meta, model=MODEL))
    history.append(Message("user", "Obrigado, resuma em uma frase."))
    r3 = await p.generate(model=MODEL, system="Atendente.", messages=history, tools=[],
                          deadline=monotonic() + 60)  # fmt: skip
    assert r3.text and not r3.tool_calls
    assert isinstance(call, ToolCall)
