import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal

import anthropic

from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall, Usage
from app.agent.retry import BACKOFF_S, RetryBudget
from app.config import AppSettings
from app.tools.base import ToolSpec
from core.clock import monotonic

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 4096  # resposta ≤ 2000 caracteres + raciocínio curto (conta no max_tokens)
EFFORT = "low"  # atendimento/chat; equivale ao MINIMAL do Gemini
BINDING_BETA = "thinking-binding-controls-2026-08-01"
# A API exige que a primeira mensagem seja `user`; o histórico começa pelo cartão (assistente).
START_TEXT = "[início do atendimento]"


async def _sleep(s: float) -> None:
    await asyncio.sleep(s)


def _default_client(settings: AppSettings) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=0)


def _text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def _messages(messages: Sequence[Message], model: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.model == model and m.provider_meta:  # raciocínio de outro modelo é descartado
                blocks += json.loads(m.provider_meta)
            if m.text:
                blocks.append(_text(m.text))
            blocks += [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": dict(c.args)}
                for c in m.tool_calls
            ]
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue
        if m.role == "tool":
            block: dict[str, Any] = {
                "type": "tool_result", "tool_use_id": m.tool_call_id,
                "content": json.dumps(dict(m.tool_result or {}), ensure_ascii=False, default=str),
            }  # fmt: skip
        elif m.role == "system_note":
            block = _text(f"[nota do sistema] {m.text}")
        else:
            block = _text(m.text or "")
        if out and out[-1]["role"] == "user":  # resultados em paralelo precisam estar juntos
            out[-1]["content"].append(block)
        else:
            out.append({"role": "user", "content": [block]})
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": [_text(START_TEXT)]})
    return out


def _parse(resp: Any, model: str) -> LLMResponse:
    u = resp.usage
    read = u.cache_read_input_tokens or 0
    # shortcut: escrita de cache cobra 1,25x na Anthropic, aqui entra a 1x; refinar se pesar.
    usage = Usage(
        input_tokens=u.input_tokens + read + (u.cache_creation_input_tokens or 0),
        output_tokens=u.output_tokens, cached_tokens=read,
    )  # fmt: skip
    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None)
        log.warning("claude recusou: category=%s", cat)
        return LLMResponse(None, (), usage, model, "BLOCKED")
    text: list[str] = []
    calls: list[ToolCall] = []
    thinking: list[Any] = []
    for b in resp.content:
        if b.type == "text":
            text.append(b.text)
        elif b.type == "tool_use":
            calls.append(ToolCall(b.id, b.name, dict(b.input)))
        elif b.type in ("thinking", "redacted_thinking"):
            thinking.append(b.model_dump(mode="json", exclude_none=True))
    meta = json.dumps(thinking, separators=(",", ":")).encode() if thinking else None
    finish = "MAX_TOKENS" if resp.stop_reason == "max_tokens" else "STOP"
    return LLMResponse("".join(text) or None, tuple(calls), usage, model, finish, meta)


def _retry_after_s(e: anthropic.APIStatusError) -> float | None:
    try:
        return float(e.response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


class AnthropicProvider:
    """Sem semáforo nem fila por chamada: a admissão é do balde global e da vaga por turno."""

    def __init__(
        self, settings: AppSettings,
        *, client_factory: Callable[[], anthropic.AsyncAnthropic] | None = None,
    ) -> None:  # fmt: skip
        self._client = (client_factory or (lambda: _default_client(settings)))()
        self._budget = RetryBudget()

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
    ) -> LLMResponse:  # fmt: skip
        msgs = _messages(messages, model)
        req: dict[str, Any] = {
            "model": model, "system": system, "messages": msgs, "max_tokens": MAX_OUTPUT_TOKENS,
            "thinking": {"type": "adaptive",
                         "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "betas": [BINDING_BETA], "output_config": {"effort": EFFORT},
            "cache_control": {"type": "ephemeral"},
        }  # fmt: skip
        if tools:
            req["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": dict(t.parameters)}
                for t in tools
            ]
        retried = False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise LLMUnavailable("timeout")
            if not retried:
                self._budget.call()
            try:
                resp = await self._client.beta.messages.create(**req, timeout=remaining)
                return _parse(resp, model)
            except anthropic.APITimeoutError as e:  # subclasse de APIConnectionError: vem antes
                raise LLMUnavailable("timeout") from e
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                code = e.status_code if isinstance(e, anthropic.APIStatusError) else 0
                if isinstance(e, anthropic.APIStatusError) and code != 429 and code < 500:
                    raise
                reason: Literal["quota", "server"] = "quota" if code == 429 else "server"
                delay = BACKOFF_S
                if isinstance(e, anthropic.APIStatusError) and code == 429:
                    delay = _retry_after_s(e) or BACKOFF_S
                now = monotonic()
                if retried or not self._budget.worth_retry(now, deadline, delay):
                    raise LLMUnavailable(reason) from e
                retried = True
                self._budget.retried(now)
                await _sleep(delay)
