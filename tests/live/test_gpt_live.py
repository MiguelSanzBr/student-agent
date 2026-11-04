"""pytest -m live tests/live/test_gpt_live.py (pula sem OPENAI_API_KEY)."""

import os

import pytest

from app.agent.gpt import OpenAIProvider
from app.agent.llm import Message
from app.agent.messages import card_messages
from app.config import AppSettings
from app.data.card import StudentCard
from app.tools.base import ToolSpec
from core.clock import monotonic

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="pendente: sem OPENAI_API_KEY"),
]
MODEL = os.environ.get("OPENAI_MODEL") or "gpt-6.1-terra"
TOOL = ToolSpec(
    "get_clima", "Devolve o clima de uma cidade.",
    {"type": "object", "properties": {"cidade": {"type": "string"}},
     "required": ["cidade"], "additionalProperties": False},
)  # fmt: skip
SYSTEM = "Responda em português. Para perguntas de clima, use a ferramenta get_clima."


async def test_roundtrip_with_card_tool_and_reasoning(app_secrets: object) -> None:
    p = OpenAIProvider(AppSettings(openai_api_key=os.environ["OPENAI_API_KEY"]))
    hist = [*card_messages(StudentCard(text="Aluno de teste", facts={})),
            Message("user", "Qual o clima em Recife?")]  # fmt: skip
    r1 = await p.generate(model=MODEL, system=SYSTEM, messages=hist, tools=[TOOL],
                          deadline=monotonic() + 60)  # fmt: skip
    assert r1.tool_calls and r1.tool_calls[0].name == "get_clima"
    c = r1.tool_calls[0]
    hist += [
        Message("assistant", r1.text, r1.tool_calls, provider_meta=r1.provider_meta, model=MODEL),
        Message("tool", tool_call_id=c.id, tool_name=c.name, tool_result={"clima": "sol, 30C"}),
    ]
    # 2ª chamada: reenvia o raciocínio (provider_meta) junto do histórico
    r2 = await p.generate(model=MODEL, system=SYSTEM, messages=hist, tools=[TOOL],
                          deadline=monotonic() + 60)  # fmt: skip
    assert r2.text and r2.finish_reason == "STOP"
    # 3ª chamada sem ferramentas (como o grafo), histórico com function_call
    hist.append(Message("assistant", r2.text, model=MODEL, provider_meta=r2.provider_meta))
    hist.append(Message("user", "Resuma em uma frase."))
    r3 = await p.generate(model=MODEL, system=SYSTEM, messages=hist, tools=[],
                          deadline=monotonic() + 60)  # fmt: skip
    assert r3.text and not r3.tool_calls
