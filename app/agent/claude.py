import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any, Literal

import anthropic

from app.agent.llm import LLMResponse, LLMUnavailable, Message, ToolCall, Usage
from app.agent.retry import BACKOFF_S, RetryBudget
from app.config import AppSettings
from app.obs.metrics import METRICS
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


_THINKING = ("thinking", "redacted_thinking")


def _text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def _assistant_blocks(m: Message, model: str) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = (
        json.loads(m.provider_meta) if m.model == model and m.provider_meta else []
    )  # raciocínio de outro modelo é descartado
    if stored:
        texts = "".join(b["text"] for b in stored if b["type"] == "text")
        uses = [(b["id"], b["name"]) for b in stored if b["type"] == "tool_use"]
        if (m.text or "") == texts and uses == [(c.id, c.name) for c in m.tool_calls]:
            return stored  # nada mudou: a ordem original dos blocos é o que a API vincula
    blocks = [b for b in stored if b["type"] in _THINKING]
    if m.text:
        blocks.append(_text(m.text))
    blocks += [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": dict(c.args)}
        for c in m.tool_calls
    ]
    return blocks


def _messages(messages: Sequence[Message], model: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            blocks = _assistant_blocks(m, model)
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue
        if m.role == "tool":
            block: dict[str, Any] = {
                "type": "tool_result", "tool_use_id": m.tool_call_id,
                "content": json.dumps(dict(m.tool_result or {}), ensure_ascii=False, default=str),
            }  # fmt: skip
        elif m.role == "system_note" and m.text:
            block = _text(f"[nota do sistema] {m.text}")
        elif m.role == "user" and m.text:  # texto vazio dá 400 na API
            block = _text(m.text)
        else:
            continue
        if out and out[-1]["role"] == "user":  # resultados em paralelo precisam estar juntos
            out[-1]["content"].append(block)
        else:
            out.append({"role": "user", "content": [block]})
    if out and out[0]["role"] == "assistant":
        out.insert(0, {"role": "user", "content": [_text(START_TEXT)]})
    return out


def _tool_names(msgs: list[dict[str, Any]]) -> set[str]:
    return {b["name"] for m in msgs for b in m["content"] if b["type"] == "tool_use"}


def _parse(resp: Any, model: str) -> LLMResponse:
    u = resp.usage
    read = u.cache_read_input_tokens or 0
    # shortcut: escrita de cache cobra 1,25x na Anthropic, aqui entra a 1x; refinar se pesar.
    thinking_tokens = getattr(getattr(u, "output_tokens_details", None), "thinking_tokens", 0) or 0
    usage = Usage(
        input_tokens=u.input_tokens + read + (u.cache_creation_input_tokens or 0),
        output_tokens=u.output_tokens - thinking_tokens, cached_tokens=read,
        thinking_tokens=thinking_tokens,
    )  # fmt: skip
    dropped = sum(
        t.type == "thinking_dropped" for t in getattr(resp, "input_transformations", None) or ()
    )
    if dropped:
        log.info("claude descartou %d bloco(s) de raciocínio (vínculo)", dropped)
        METRICS["agent_llm_thinking_dropped_total"].inc(dropped)  # type: ignore[union-attr]
    if resp.stop_reason == "refusal":
        cat = getattr(resp.stop_details, "category", None)
        log.warning("claude recusou: category=%s", cat)
        return LLMResponse(None, (), usage, model, "BLOCKED")
    text: list[str] = []
    calls: list[ToolCall] = []
    blocks: list[Any] = []  # todos os blocos, na ordem (o raciocínio é vinculado à posição)
    for b in resp.content:
        if b.type == "text":
            text.append(b.text)
        elif b.type == "tool_use":
            calls.append(ToolCall(b.id, b.name, dict(b.input)))
        elif b.type not in _THINKING:
            continue
        blocks.append(b.model_dump(mode="json", exclude_none=True))
    has_thinking = any(b["type"] in _THINKING for b in blocks)
    meta = json.dumps(blocks, separators=(",", ":")).encode() if has_thinking else None
    finish = "MAX_TOKENS" if resp.stop_reason == "max_tokens" else "STOP"
    return LLMResponse("".join(text) or None, tuple(calls), usage, model, finish, meta)


def _retry_after_s(e: anthropic.APIStatusError) -> float | None:
    try:
        return float(e.response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _classify(
    e: anthropic.APIStatusError | anthropic.APIConnectionError,
) -> tuple[Literal["quota", "server"], float]:
    """Motivo e espera da nova tentativa; outros 4xx propagam."""
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code == 429:
            return "quota", _retry_after_s(e) or BACKOFF_S
        if e.status_code < 500:
            raise e
    return "server", BACKOFF_S


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
        elif names := sorted(_tool_names(msgs)):
            # A API recusa tool_use/tool_result sem `tools`: declara só os nomes do histórico
            # e proíbe chamar (rodada de feedback da guarda / última rodada).
            req["tools"] = [
                {"name": n, "description": "(só histórico)", "input_schema": {"type": "object"}}
                for n in names
            ]
            req["tool_choice"] = {"type": "none"}
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
                reason, delay = _classify(e)
                now = monotonic()
                if retried or not self._budget.worth_retry(now, deadline, delay):
                    raise LLMUnavailable(reason) from e
                retried = True
                self._budget.retried(now)
                await _sleep(delay)
