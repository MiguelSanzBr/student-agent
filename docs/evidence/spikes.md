# Spikes da Task 3 (2026-11-04)

Testes: `tests/live/test_spikes.py` (marca `live`). Dados brutos e pins: `docs/evidence/spikes.json`.
Python 3.12.16. Pins: a2a-sdk 1.3.0 (`[http-server]`), langgraph 1.2.19, google-genai 2.34.0, opentelemetry-api/sdk/exporter-otlp-proto-http 1.45.2,
openinference-semantic-conventions 0.1.43, arize-phoenix-otel 0.17.2, arize-phoenix-client 3.6.0,
imagem `arizephoenix/phoenix:version-21.0.0` (sha256:1f9a144a…eed3fbaad).

## A2A 1.0 (executado)
Cliente real do SDK (`ClientFactory`) contra `DefaultRequestHandler` + `InMemoryTaskStore` (dono = `user_name` do `ServerCallContext`), em processo via ASGI.

| Caso | Observado |
|---|---|
| `GetTask` / `CancelTask` / `SendMessage(taskId)` de tarefa alheia | `TaskNotFound` -32001 (controle positivo do dono passa) |
| `SendMessage(taskId)` do dono com tarefa terminal | `UnsupportedOperation` -32004 |
| `ListTasks` de outro aluno | lista vazia; cada um vê só as próprias |
| `contextId` alheio | **aceito**: cria tarefa nova do chamador com o mesmo `context_id`. O isolamento da conversa é do executor (`rkey("conv", sub, ...)`) |
| `CancelTask` concluída | `TaskNotCancelable` -32002 |
| 4 métodos de push config | -32003 |
| `SendStreamingMessage`, `SubscribeToTask` (streaming=False) | -32004 |
| `GetExtendedAgentCard` com `extended_agent_card=False` | **-32004** (não -32007) |
| `GetExtendedAgentCard` com `extended_agent_card=True` sem card | -32007 |
| método inexistente | -32601 |
| executor publica REJECTED + `metadata.retry_after_s` | cliente recebe Task REJECTED, `metadata.retry_after_s == 7.0`, mensagem de status presente |
| exceção no executor, TaskStore ou context builder | `InternalError(str(e))` com a mensagem **original** (vaza) e log nos loggers `a2a.*`; a tarefa vira FAILED. Confirma a necessidade dos wrappers e do executor que captura tudo |
| `logging.getLogger("a2a").setLevel(CRITICAL+1)` | silencia todos os registros do SDK |

Valores fixados em `tests/e2e/a2a_codes.py`. Achado: o card do plano (extended=False) devolve -32004 em `GetExtendedAgentCard`; os testes dos 11 métodos devem esperar `EXTENDED_CARD_UNSUPPORTED` (-32004) com o card declarado e -32007 só se `extended_agent_card=True`.
Detalhes do SDK 1.3.0: `DefaultRequestHandler` = V2; o executor precisa enfileirar o `Task` inicial (`new_task_from_user_message`) antes do `TaskUpdater`; `update_status(..., metadata=)` vira `Task.metadata`; o card sem `skills` gera aviso.

## LangGraph (executado)
Grafo de 2 nós: `get_stream_writer()` + `stream_mode="custom"` entrega `{"fase":"a"}` e `{"fase":"b"}`; `task.cancel()` do consumidor propaga `CancelledError` no nó em `await`; `get_graph().draw_mermaid()` funciona.

## Phoenix (executado)
Container `sa-spike-phoenix` (6016→6006, 4327→4317), removido ao fim. Span raiz com `BatchSpanProcessor` → `force_flush(2000)` em `asyncio.to_thread` com timeout de 2,5 s → span visível via `arize-phoenix-client` → anotação `thumb` gravada e lida (`/v1/projects/default/span_annotations`). Com `docker restart` e volume em `PHOENIX_WORKING_DIR=/mnt/data`, span e anotação persistem.
**Armadilha:** a imagem define `PHOENIX_HOST=::`; um host não-loopback liga a autenticação por padrão (401 "Invalid token" em tudo). O compose deve definir `PHOENIX_ENABLE_AUTH=false` (rede interna) ou provisionar segredos.

## Gemini (NADA foi executado: pendente, sem GEMINI_API_KEY)
Nenhuma chamada ao Gemini foi feita na Task 3. Os testes abaixo existem em `tests/live/test_spikes.py` (`-m live`), pulam sem chave e rodam sem alteração quando a chave existir. Foram só coletados (collect-only/skip): podem precisar de ajuste na primeira execução real. Observações vão para `docs/evidence/gemini_observations.json`; amostras do portão para `docs/evidence/gemini_samples_<modelo>.json`.

| Item | Teste | Estado |
|---|---|---|
| `models.list` contém os 2 IDs (senão, lista os disponíveis) | `test_gemini_models_listed` | pendente: sem GEMINI_API_KEY |
| schema estrito (`enum`, `additionalProperties:false`) validado com jsonschema | `test_gemini_strict_schema_enum_no_additional_properties` | pendente |
| 2 voltas com `thought_signature` devolvida | `test_gemini_two_turns_thought_signature_roundtrip` | pendente |
| portão 20 emitem ferramenta + 20 respondem, MINIMAL, ~1.500 tokens, 11 declarações | `test_gemini_latency_gate` | pendente |
| `cached_content_token_count` na 2ª requisição de outro aluno, mesmo prefixo | `test_gemini_cached_tokens_second_student_same_prefix` | pendente |
| `max_output_tokens` × raciocínio (MINIMAL/LOW) | `test_gemini_max_output_tokens_vs_thinking` | pendente |
| troca de modelo carregando a assinatura do outro | `test_gemini_model_switch_carries_other_models_signature` | pendente |
| `functionCall` sintético `get_student_card` + `functionResponse` (declarada? assinatura?) | `test_gemini_synthetic_get_student_card_call` | pendente |
| RPM/TPM/RPD por modelo | `test_gemini_quotas_manual` (manual: página de cotas) | pendente; valores em `slo_targets.yaml` são provisórios |
| Vertex `global`, 5 chamadas | `test_vertex_global_five_calls` | pendente: sem `GOOGLE_CLOUD_PROJECT` |

## Fórmula de latência
`evals/latency/formula.py` (stdlib). **Os componentes abaixo são PRIORS, não medidos**; devem ser substituídos pelos lognormais ajustados (`fit_lognormal`) com as amostras do portão.
Priors: chamada que emite ferramenta / chamada que responde, lognormal sigma 0,35, medianas 1,8 s / 2,6 s (flash-lite) e 2,6 s / 3,8 s (3.8-flash); SQL+guard+overhead lognormal de mediana 0,3 s (sigma 0,2); retry do guard 5%; k de ferramentas: `card` 0, `1` 1, `2+` sorteado de {2,2,3,4}; n=200.000, seed 42.
TTFE = 1ª chamada que emite ferramenta (1º `progress`); `card` = latência total (no `targets()`, TTFE do `card` = p95 final). O TTFE de 1 e 2+ **exclui** o overhead anterior ao 1º progress (admissão, prompt), logo é otimista por esse valor. Metas iniciais da spec: card p50 3,5 / p95 6; `1` p95 8; `2+` p95 12; TTFE (1, 2+) p95 4.

Metas = max(inicial, previsto), em segundos:

| Modelo | Classe | p50 | p90 | p95 | p99 | TTFE p95 |
|---|---|---|---|---|---|---|
| flash-lite | card | 3,5 | 4,73 | 6,0 | 7,25 | 6,0 |
| flash-lite | 1 | 4,91 | 6,92 | 8,0 | 9,41 | 4,0 |
| flash-lite | 2+ | 8,19 | 11,6 | 12,59 | 14,59 | 4,0 |
| 3.8-flash | card | 4,19 | 6,77 | 7,9 | 10,46 | 7,9 |
| 3.8-flash | 1 | 7,0 | 9,92 | 11,08 | 13,56 | 4,63 |
| 3.8-flash | 2+ | 11,74 | 16,68 | 18,12 | 21,01 | 4,62 |

Modelo padrão: **gemini-3.8-flash** (`slo_targets.yaml` usa as linhas dele); flash-lite é o fallback. Orçamento do grafo (p99 `2+` × 1,2): flash-lite **17,51 s**; 3.8-flash **25,22 s**. Observação: com os priors, a meta inicial `2+` p95 < 12 s já fica abaixo do previsto (12,59 s no flash-lite) e a fórmula a eleva; com o 3.8-flash, `card` e `1` também superam as metas iniciais. Cotas: pendente: sem GEMINI_API_KEY. A proposta para `slo_targets.yaml` está em `spikes.json` → `slo_targets_proposal` (já mesclada em `slo_targets.yaml`).
