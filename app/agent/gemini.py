import asyncio
import logging
import re
from collections.abc import Callable, Sequence
from typing import Any, Literal

import httpx
from google import genai
from google.genai import errors, types

from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall, ToolChoice, Usage
from app.agent.retry import BACKOFF_S, RetryBudget
from app.config import AppSettings
from app.tools.base import ToolSpec
from core.clock import monotonic

log = logging.getLogger(__name__)
THINKING_LEVEL = "MINIMAL"  # nível do portão (spec §6.5); medição real pendente: sem GEMINI_API_KEY
MAX_OUTPUT_TOKENS = 2048  # resposta ≤ 2000 caracteres + raciocínio mínimo; a confirmar no spike


async def _sleep(s: float) -> None:
    await asyncio.sleep(s)


def _default_client(settings: AppSettings) -> genai.Client:
    http = types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))
    if settings.llm_backend == "vertex":
        return genai.Client(
            vertexai=True, project=settings.gcp_project, location=settings.gcp_location,
            http_options=http,
        )  # fmt: skip
    return genai.Client(api_key=settings.gemini_api_key, http_options=http)


def _retry_info_s(e: errors.APIError) -> float | None:
    body = e.details if isinstance(e.details, dict) else {}
    err = body.get("error")
    details = (err if isinstance(err, dict) else body).get("details")
    for d in details if isinstance(details, list) else []:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
            m = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
            return float(m.group(1)) if m else None
    return None


def _contents(messages: Sequence[Message], model: str) -> list[types.Content]:
    out: list[types.Content] = []

    def add(role: str, part: types.Part) -> None:
        if out and out[-1].role == role and role == "user":  # respostas de ferramenta em paralelo
            assert out[-1].parts is not None
            out[-1].parts.append(part)
        else:
            out.append(types.Content(role=role, parts=[part]))

    for m in messages:
        keep = m.model == model  # assinatura de outro modelo é descartada
        if m.role == "user":
            add("user", types.Part(text=m.text or ""))
        elif m.role == "system_note":
            add("user", types.Part(text=f"[nota do sistema] {m.text}"))
        elif m.role == "tool":
            add("user", types.Part.from_function_response(
                name=m.tool_name or "", response=dict(m.tool_result or {})))  # fmt: skip
        else:
            parts = (
                [types.Part(text=m.text, thought_signature=m.provider_meta if keep else None)]
                if m.text
                else []
            )
            parts += [
                types.Part(
                    function_call=types.FunctionCall(name=c.name, args=dict(c.args)),
                    thought_signature=c.provider_meta if keep else None,
                )
                for c in m.tool_calls
            ]
            if parts:
                out.append(types.Content(role="model", parts=parts))
    return out


def _parse(resp: Any, model: str) -> LLMResponse:
    cand = resp.candidates[0] if resp.candidates else None
    if cand is None:  # prompt barrado: sem candidatos
        reason = getattr(getattr(resp, "prompt_feedback", None), "block_reason", None)
        log.warning("gemini sem candidatos: block_reason=%s", getattr(reason, "name", reason))
        u = resp.usage_metadata
        usage = Usage(input_tokens=(u.prompt_token_count or 0) if u else 0)
        return LLMResponse(None, (), usage, model, "BLOCKED")
    text: list[str] = []
    calls: list[ToolCall] = []
    meta: bytes | None = None
    for p in (cand.content.parts or []) if cand and cand.content else []:
        if p.function_call:
            fc = p.function_call
            calls.append(ToolCall(fc.id or f"call_{len(calls)}", fc.name or "",
                                  dict(fc.args or {}), p.thought_signature))  # fmt: skip
        elif p.text and not p.thought:
            text.append(p.text)
            meta = p.thought_signature or meta
    u = resp.usage_metadata
    usage = Usage(
        input_tokens=(u.prompt_token_count or 0) if u else 0,
        output_tokens=(u.candidates_token_count or 0) if u else 0,
        thinking_tokens=(u.thoughts_token_count or 0) if u else 0,
        cached_tokens=(u.cached_content_token_count or 0) if u else 0,
    )
    fr = cand.finish_reason if cand else None
    return LLMResponse(
        "".join(text) or None, tuple(calls), usage, model,
        getattr(fr, "name", None) or str(fr or "STOP"), meta,
    )  # fmt: skip


class GeminiProvider:
    """Sem semáforo nem fila por chamada: a admissão é do balde global e da vaga por turno."""

    def __init__(
        self, settings: AppSettings, *, client_factory: Callable[[], genai.Client] | None = None
    ) -> None:
        self._client = (client_factory or (lambda: _default_client(settings)))()
        self._budget = RetryBudget()

    async def generate(
        self, *, model: str, system: str, messages: Sequence[Message],
        tools: Sequence[ToolSpec], deadline: float,
        tool_choice: ToolChoice = "auto",
    ) -> LLMResponse:  # fmt: skip
        contents = _contents(messages, model)
        decls = [types.FunctionDeclaration(name=t.name, description=t.description,
                                           parameters_json_schema=dict(t.parameters))
                 for t in tools]  # fmt: skip
        retried = False
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise LLMUnavailable("timeout")
            cfg = types.GenerateContentConfig(
                system_instruction=system,
                tools=[types.Tool(function_declarations=decls)] if decls else None,
                tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE)) if tool_choice == "none" else None,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL),
                max_output_tokens=MAX_OUTPUT_TOKENS,
                http_options=types.HttpOptions(
                    timeout=int(remaining * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )  # fmt: skip
            if not retried:
                self._budget.call()
            try:
                resp = await self._client.aio.models.generate_content(
                    model=model, contents=contents, config=cfg
                )
                return _parse(resp, model)
            except errors.APIError as e:
                code = e.code
                if code != 429 and not 500 <= code < 600:
                    raise
                reason: Literal["quota", "server"] = "quota" if code == 429 else "server"
                delay = _retry_info_s(e) if code == 429 else None
                delay = BACKOFF_S if delay is None else delay
                now = monotonic()
                if retried or not self._budget.worth_retry(now, deadline, delay):
                    raise LLMUnavailable(reason) from e
                retried = True
                self._budget.retried(now)
                await _sleep(delay)
            except (TimeoutError, httpx.TimeoutException) as e:
                raise LLMUnavailable("timeout") from e
