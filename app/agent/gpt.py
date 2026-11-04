import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal

import openai

from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall, Usage
from app.agent.retry import BACKOFF_S, RetryBudget
from app.config import AppSettings
from app.tools.base import ToolSpec
from core.clock import monotonic

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 4096  # o raciocínio conta no teto
# Modelos que aceitam reasoning.effort="none" (página de modelos da OpenAI, 2026-11-04);
# os demais usam "low". Medição real pendente: sem OPENAI_API_KEY.
EFFORT_NONE_MODELS = ("gpt-6.1-terra", "gpt-6-luna")


async def _sleep(s: float) -> None:
    await asyncio.sleep(s)


def _default_client(settings: AppSettings) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(api_key=settings.openai_api_key, max_retries=0)


def _retry_after_s(e: openai.APIStatusError) -> float | None:
    h = e.response.headers
    try:
        if (ms := h.get("retry-after-ms")) is not None:
            return float(ms) / 1000
        if (s := h.get("retry-after")) is not None:
            return float(s)
    except ValueError:
        pass
    return None


def _input(messages: Sequence[Message], model: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.text or ""})
        elif m.role == "system_note":
            out.append({"role": "user", "content": f"[nota do sistema] {m.text}"})
        elif m.role == "tool":
            out.append({
                "type": "function_call_output", "call_id": m.tool_call_id,
                "output": json.dumps(dict(m.tool_result or {}), ensure_ascii=False, default=str),
            })  # fmt: skip
        else:
            if m.model == model and m.provider_meta:  # raciocínio de outro modelo é descartado
                out.extend(json.loads(m.provider_meta))
            if m.text:
                out.append({"role": "assistant", "content": m.text})
            out.extend(
                {"type": "function_call", "call_id": c.id, "name": c.name,
                 "arguments": json.dumps(dict(c.args), ensure_ascii=False)}
                for c in m.tool_calls
            )  # fmt: skip
    return out


def _parse(resp: Any, model: str) -> LLMResponse:
    text: list[str] = []
    calls: list[ToolCall] = []
    reasoning: list[Any] = []
    refused = False
    for it in resp.output or []:
        if it.type == "message":
            for c in it.content:
                if c.type == "output_text":
                    text.append(c.text)
                elif c.type == "refusal":
                    refused = True
        elif it.type == "function_call":
            try:
                args = json.loads(it.arguments or "{}")
                if not isinstance(args, dict):
                    raise ValueError("argumentos não são um objeto")
            except ValueError:
                log.warning("gpt: argumentos de %s não são JSON válido", it.name)
                args = {}
            calls.append(ToolCall(it.call_id, it.name, args))
        elif it.type == "reasoning":
            reasoning.append(it.model_dump(mode="json", exclude_none=True))
    u = resp.usage
    thinking = getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    out_total = getattr(u, "output_tokens", 0) or 0
    usage = Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=out_total - thinking,  # o output_tokens da OpenAI inclui o raciocínio
        thinking_tokens=thinking,
        cached_tokens=getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0,
    )
    why = getattr(resp.incomplete_details, "reason", None) if resp.status == "incomplete" else None
    if refused or why == "content_filter":
        log.warning("gpt bloqueado: refusal=%s incomplete=%s", refused, why)
        return LLMResponse(None, (), usage, model, "BLOCKED")
    meta = json.dumps(reasoning, separators=(",", ":")).encode() if reasoning else None
    return LLMResponse(
        "".join(text) or None, tuple(calls), usage, model,
        "MAX_TOKENS" if why == "max_output_tokens" else "STOP", meta,
    )  # fmt: skip


class OpenAIProvider:
    """Responses API, sem estado no servidor (`store=False`); a nova tentativa é nossa."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        client_factory: Callable[[], openai.AsyncOpenAI] | None = None,
    ) -> None:
        self._client = (client_factory or (lambda: _default_client(settings)))()
        self._budget = RetryBudget()

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
    ) -> LLMResponse:  # fmt: skip
        effort = "none" if model in EFFORT_NONE_MODELS else "low"
        kw: dict[str, Any] = {
            "model": model, "instructions": system, "input": _input(messages, model),
            "max_output_tokens": MAX_OUTPUT_TOKENS, "store": False,
            "reasoning": {"effort": effort},
        }  # fmt: skip
        if tools:
            kw["tools"] = [
                {"type": "function", "name": t.name, "description": t.description,
                 "parameters": dict(t.parameters), "strict": False}
                for t in tools
            ]  # fmt: skip
        if effort != "none":  # com store=False é o único jeito de reenviar o raciocínio
            kw["include"] = ["reasoning.encrypted_content"]
        retried = False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise LLMUnavailable("timeout")
            if not retried:
                self._budget.call()
            try:
                resp = await self._client.responses.create(**kw, timeout=remaining)
                return _parse(resp, model)
            except (openai.APITimeoutError, TimeoutError) as e:  # antes: é subclasse de conexão
                raise LLMUnavailable("timeout") from e
            except (openai.APIStatusError, openai.APIConnectionError) as e:
                code = e.status_code if isinstance(e, openai.APIStatusError) else 599
                if code != 429 and code < 500:
                    raise
                reason: Literal["quota", "server"] = "quota" if code == 429 else "server"
                delay = _retry_after_s(e) if isinstance(e, openai.APIStatusError) else None
                delay = BACKOFF_S if delay is None or code != 429 else delay
                now = monotonic()
                if retried or not self._budget.worth_retry(now, deadline, delay):
                    raise LLMUnavailable(reason) from e
                retried = True
                self._budget.retried(now)
                await _sleep(delay)
