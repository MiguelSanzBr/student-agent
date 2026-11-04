# Observabilidade (Task 29)

Smoke real contra `arizephoenix/phoenix:version-21.0.0@sha256:1f9a144a…` (profile `obs`, projeto compose
`sa-t29`, porta 6029, derrubado com `down -v`). Teste: `PHOENIX_URL=http://127.0.0.1:6029 uv run pytest
tests/e2e/test_observability.py` (passa; pulado sem `PHOENIX_URL`). Um turno com LLM roteirizado
(sem GEMINI_API_KEY), uma anotação 👎 e o export/reimport.

## Árvore de spans recebida no Phoenix (um turno do cartão, sem ferramenta)

| span | kind | pai | atributos |
|---|---|---|---|
| `chat.turn` | AGENT | — | `channel=web`, `llm.calls=1`, `guard.outcome=pass`, `turn.status=ok`, `turn.tools_class=card`, `turn.answer_kind=resposta`, `prompt.sha`, `tools.sha`, `session.id` (HMAC) |
| `agent.call_model` | LLM | `chat.turn` | `llm.model_name`, `gen_ai.request.model`, `llm.token_count.prompt=100`, `.completion=10`, `.prompt_details.cache_read=0`, `.completion_details.reasoning=0`, `gen_ai.usage.*` |
| `agent.guard` | GUARDRAIL | `chat.turn` | `guard.outcome=pass`, `guard.kinds=""` |

Sem `input.value`/`output.value` (conteúdo só em dev+eval). Spans `agent.tool` (TOOL) com `tool.name`,
`tool.args.<enum>`, `tool.args.count`, `tool.returned_records` ou `tool.failed` aparecem nos turnos
com ferramenta (coberto por `test_tool_args_enumerated_no_pii`).

## Anotação e export

- `annotate(trace_id, "thumb", "down", 0.0, "smoke")` → worker → `force_flush` em thread (2 s) →
  `span_annotations` do span raiz `chat.turn`: `thumb {label: down, score: 0.0}` lida de volta.
- `scripts.phoenix_export export <projeto>` → `<projeto>.json` (3 spans + 1 anotação); após apagar o
  projeto, `import` recria os 3 spans e a anotação (`export` de novo: 3 spans, 1 anotação).
  Importar sobre um Phoenix que já tem os spans falha com `SpanCreationError` (ids duplicados): o
  destino do import é um Phoenix limpo.

## Métricas

`GET /metrics` serve o texto Prometheus; com `PROMETHEUS_MULTIPROC_DIR` (compose) soma os processos.
Buckets de `agent_turn_seconds`: 0.5, 1, 2, 3, 4, 6, 8, 12, 16, 24 (`slo_targets.yaml`).
