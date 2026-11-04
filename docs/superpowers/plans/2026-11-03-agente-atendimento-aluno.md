# Agente de atendimento ao aluno — plano de implementação (v5, congelado)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agente que atende o aluno autenticado com isolamento por construção, respostas presas à evidência e evidências medidas de qualidade, segurança, latência e observabilidade.

**Architecture:**
- **ETL:** resolve as entidades dos 4 sistemas num Postgres canônico. O RLS forçado é aplicado depois da carga; não há dado interno e o texto livre chega tipificado.
- **API:** FastAPI stateless; a identidade vem só do JWT.
- **`TurnService`:** comum ao chat SSE e ao A2A 1.0. Admite o turno contra a cota global antes do stream, monta o cartão do aluno e roda um `StateGraph` do LangGraph sobre o `google-genai`.
- **Ferramentas:** tipadas, sem identidade, com recusa na execução.
- **EvidenceGuard:** determinístico e ancorado no registro citado; toda resposta passa por ele.

**Tech Stack:** Python 3.12, uv, FastAPI, psycopg 3 + psycopg_pool, Postgres 17, Redis 7, `google-genai` 2.34, `langgraph` 1.2, `a2a-sdk[http-server]` 1.3, `jsonschema`, `pyjwt`, `structlog`, `opentelemetry-sdk` + `openinference-semantic-conventions` + `arize-phoenix-otel` + `arize-phoenix-client` (versões fixadas no spike), `prometheus-client`, pytest (+ pytest-asyncio, fakeredis, hypothesis), ruff, mypy, `opentimestamps-client`.

**Spec:** [docs/superpowers/specs/2026-11-03-agente-atendimento-aluno-design.md](../specs/2026-11-03-agente-atendimento-aluno-design.md) (v5).

**Base:** onde este plano diz "como na v4", vale o texto da tarefa de mesmo número em [plano v4](2026-11-03-agente-atendimento-aluno-v4.md) (passos, testes, interfaces), acrescido do que está aqui; em conflito, vale esta v5. Pesquisa: [docs/research/2026-11-03-perfil-dos-dados.md](../../research/2026-11-03-perfil-dos-dados.md). Case: `data/case/README.md`. Agenda pessoal do autor: fora do repositório.

## Global Constraints

- **Commits:** autor `Miguel Sanz <miguelsanzb.p@gmail.com>`, Conventional Commits em PT-BR, sem trailers.
- **Repositório:** nenhum código de outro projeto; `data/` e segredos fora do git.
- **Vocabulário:** *modelo* = LLM; *template de pergunta* / *template de ataque* = moldes do dataset.
- **Idioma:** identificadores em inglês. Prompt, respostas, chaves lidas pelo LLM e docs em PT-BR. O servidor troca os códigos de serviço pelos nomes PT na saída.
- **Relógio:** só `core/clock.py`. A regex `(?<![\w.])now\(|CURRENT_DATE|CURRENT_TIMESTAMP|date\.today|datetime\.now|datetime\.utcnow|time\.time\(` vale em `core/`, `app/`, `etl/`, `evals/` (exceto `evals/latency/`) e `*.sql`.
- **Redis:** todas as chaves são `RKey = NewType("RKey", str)`, criadas por `rkey(kind, scope: UUID | Literal["global"], channel, id)`. Os clientes Redis aceitam só `RKey`, o que o mypy checa.
- **Ferramentas:**
  - as 11 sempre declaradas, com `enrollment` ∈ {"1","2","3"};
  - `additionalProperties:false` via `parameters_json_schema`;
  - `ToolContext` recriado a cada rodada, com recusa via `Tool.allowed`;
  - no máximo 20 itens + `total`.
- **Formatos:** `R$ 1.234,56`; `dd/mm/aaaa`; nota com 1 casa decimal.
- **Fonte única de números operacionais:** `slo_targets.yaml` (timeouts, orçamento do grafo, metas, buckets). O plano não fixa esses números.
- **Limites (perfil `default`):**

  | Item | Limite |
  |---|---|
  | Entrada | ≤ 2000 caracteres |
  | Mensagens | 20/min por aluno |
  | Tokens | 200.000/dia por aluno |
  | Teto global (demo) | US$ 20/dia |
  | Histórico | 5 turnos, TTL 1800 s |
  | JWT | 30 min |
  | Rascunho | 600 s |
  | Ticket de login | 300 s |
  | Rodadas | ≤ 4 |
  | `recursion_limit` | 2·rounds + 6 |

  - Retry só em 429/5xx, ≤ 10% com piso de 3/min.
  - Perfil `eval`: teto de US$ 40, sem degradação de modelo; inválido só com 429/5xx.
- **Banco:**
  - `canon` (recriado) e `agent_rw` (persistente, sem FK para `canon`);
  - roles `etl_owner` NOSUPERUSER e `agent_app` NOBYPASSRLS, com grants por allowlist;
  - carga: tabelas → COPY → relatório **em memória** → RLS/grants;
  - conexão por chamada de ferramenta, nunca segurada durante o `await` do LLM.
- **Segredos:**
  - `init-secrets` gera todos (Postgres superusuário, `etl_owner`, `agent_app`, Redis, JWT, dev, canário com 32 hex, HMAC) e copia a `GEMINI_API_KEY` do `.env`;
  - um volume por consumidor (`db`/`redis` 999; `app`/`etl`/`evals` 10001), modo 0400;
  - também cria os volumes `etl-out` e `eval-out` com dono 10001;
  - os settings leem segredos só de arquivo;
  - a API não monta `data/` nem recebe o `.env`.
- **Escrita:**
  - `AGENT_SERVICES` + elegibilidade;
  - só pelo web e sem taint;
  - rascunho idempotente por (conversa, serviço);
  - `pg_advisory_xact_lock` na confirmação (exceto GEN-090/OUV-099).
- **A2A 1.0:**
  - `enable_v0_3_compat=False`;
  - testes nos 11 métodos;
  - `ListTasks` filtra pelo dono;
  - o executor captura toda exceção;
  - `OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false`.
- **Números congelados:** se divergirem, **parar e escalar**; nunca ajustar o oráculo ou o código para passar.

  | Número | Valor |
  |---|---|
  | Protocolos ligados | 3911 (3455/73/29/337/17) |
  | Protocolos sem vínculo | 22 |
  | Contas LMS | 1003 |
  | Snapshot financeiro | 273/273 |
  | Provas encerradas sem tentativa / simulados abertos | 0 / 1217 |
  | Logins de analista | 29 |
  | Golden 4 | R$ 49.508,43 |

- **Paralelismo:** uma worktree e um banco (`CREATE DATABASE … TEMPLATE`) por raia; no máximo 4 subagentes simultâneos.
- **Lint:** ruff (linha 100); `mypy --strict` em `core/`, `etl/`, `app/guards/`, `app/rules/` e `app/infra/`.
- **Revisão:**
  - por tarefa: 1, 2, 11, 12, 13, 14, 18, 20, 21, 22, 24, 25, 26, 27 e 29; as demais por fase;
  - modelo mais barato só nas tarefas 5, 7 e 23;
  - `ponytail-review` e cobertura por fase;
  - `security-review` + `code-review` **sexta à noite**, enquanto a carga roda.
- **Skills:** TDD e verification-before-completion sempre; `fastapi-expert` nas tarefas 24–29.
- **Congelamento no M4:** depois dele, só correções de vazamento ou de falha grave. Qualquer correção em `app/agent|guards|tools|rules` exige refazer 1 rodada de ataques e os itens afetados, regravar o replay e declarar o SHA e o delta.

## Review Focus

1. **Golden 9 (ativa + formada) pergunta sobre o diploma:** a resposta usa a matrícula certa e cita o diploma emitido. Tasks 18, 20, 22.
2. **"Ver provas → pedir 2ª chamada"** funciona sem taint (as salas são tipificadas, inclusive as sem `idnumber`); texto plantado bloqueia. Tasks 12, 22.
3. **A banca roda depois de 10/11:** respostas idênticas. Tasks 1, 15, 17.
4. **Cota global esgotada:** HTTP 503 + `Retry-After` antes do stream. Durante o turno, retry só com tempo de sobra; depois, `error` em PT-BR. Vale também no A2A. Tasks 4, 25, 27.
5. **Pergunta com premissa falsa ou promessa** ("me forma em 2027/2?", "pode parcelar em 10x?"): o guard bloqueia e a resposta corrige com a fonte. Tasks 21, 22.

---

## Mapa de arquivos

Igual ao da v4, com estes acréscimos:
- `slo_targets.yaml`;
- `ontology/text_fields.yaml` e `guard_nouns.yaml`;
- `app/obs/annotations.py` (fachada criada na Task 2);
- `scripts/plant.py` (usado pelo `etl --plant`);
- `ops/compose.eval.yml` (`api-redteam`);
- `data/holdout/` (fora do git);
- `docs/evidence/phoenix/`.

## Interfaces centrais (criadas na Task 2)

```python
# core/rkey.py
RKey = NewType("RKey", str)
def rkey(kind: str, scope: UUID | Literal["global"], channel: Literal["web", "a2a", "-"], id: str) -> RKey: ...

# core/scope.py — EnrollmentRef, StudentScope (como v4)
# app/data/card.py — StudentCard(text, facts)

# app/tools/base.py
@dataclass(frozen=True) class ToolSpec: name: str; description: str; parameters: dict[str, Any]
@dataclass(frozen=True)
class DraftCard:
    draft_id: str; token: str; service_code: str; service_name_pt: str; enrollment_label: str | None
    fee: str; due_date: str; description: str; warning: str
    def public(self) -> dict[str, str]: ...
@dataclass(frozen=True)
class ToolOutput: data: dict[str, Any]; untrusted: bool = False; draft: DraftCard | None = None; records: tuple[dict, ...] = ()
@dataclass(frozen=True)
class ToolContext:
    scope: StudentScope; settings: AppSettings; channel: Literal["web", "a2a"]
    conversation_id: str; tainted: bool; round: int
    db: Callable[[], AbstractAsyncContextManager[AsyncConnection]]   # conexão por chamada
    drafts: DraftStore
@dataclass(frozen=True)
class Tool: spec: ToolSpec; handler: Handler; read_only: bool = True; allowed: Callable[[ToolContext], str | None] = ...

# app/agent/llm.py — ToolCall, Usage, LLMResponse(model, finish_reason, provider_meta), Message(model, untrusted, turn),
# LLMProvider.generate(*, model, system, messages, tools, deadline), LLMUnavailable(reason)

# app/agent/graph.py
@dataclass(frozen=True)
class TurnResult:
    answer: str; status: Literal["ok","degraded","fallback","budget_exceeded","llm_unavailable","blocked_limit","shed","fixed"]
    guard: Literal["pass","retry_pass","degraded","fallback","n/a"]; draft: DraftCard | None
    tool_calls: tuple[str, ...]; tools_class: Literal["card","1","2+"]; llm_calls: int; model: str
    usage: Usage; timings_ms: dict[str, float]; first_event_ms: float | None
    pre_guard_answer: str | None; new_messages: tuple[Message, ...]

# app/service/turn.py
@dataclass(frozen=True) class Admission: ok: bool; status: int; retry_after_s: int | None; ticket: str | None
class TurnService:
    async def admit(self, *, scope: StudentScope, channel: Literal["web","a2a"]) -> Admission: ...
    async def handle(self, *, admission: Admission, scope: StudentScope, conversation_id: str, text: str,
                     channel: Literal["web","a2a"], emit: Callable[[dict], Awaitable[None]]) -> TurnResult: ...

# app/infra/redis_stores.py (stubs na T2): AdmissionBucket(reserve/settle), RateLimit, TokenBudget, GlobalSpend,
# HistoryStore, DraftStore(put/take/pending/same_pending), LoginTickets, LabelCache, RedisTaskStore
# app/obs/tracing.py span(name, kind, **attrs); app/obs/metrics.py (declarativo); app/obs/annotations.py annotate(...) no-op
```

## Cronograma, estimativas e marcos

**P50 / P90, em horas de agente:**

| Task | P50 | P90 | | Task | P50 | P90 |
|---|---|---|---|---|---|---|
| 1 Base | 1,75 | 2,75 | | 19 LMS + catálogo | 1,25 | 2 |
| 2 Contratos | 1,25 | 2 | | 20 Cartão + exposição + LLM comprometido | 1,5 | 2,5 |
| 3 Spikes | 2 | 3 | | 21 Guards + perturbação | 3,5 | 5 |
| 4 Provider + admissão | 2,5 | 3,5 | | 22 Prompt + grafo | 4 | 5,5 |
| 5 Normalização | 0,5 | 0,75 | | 23 Replay | 0,5 | 0,75 |
| 6a Raw + fixture | 1,5 | 2 | | 24 Auth + dev | 1,25 | 2 |
| 6b Oráculo | 4 | 6 | | 25 TurnService + SSE + smoke | 3 | 4 |
| 7 Ontologia | 1,75 | 2,5 | | 26 Confirmação + feedback | 1,25 | 1,75 |
| 8 Resolução core | 0,75 | 1 | | 27 A2A | 3 | 4,5 |
| 9 Resolução LMS | 0,75 | 1 | | 28 Web | 0,75 | 1 |
| 10 Resolução secretaria | 1 | 1,5 | | 29 Observabilidade | 2,5 | 3,5 |
| 11 Schema + RLS + grants | 1,25 | 1,75 | | 30 Dataset + análise + score | 3,5 | 4,5 |
| 12 Build | 2,25 | 3,25 | | 31 Qualidade (código + rodadas) | 2,5 + 3 | 4 + 4 |
| 13 Carga + relatório | 1,25 | 2 | | 32 Ataques (código + rodadas) | 3,5 + 1,5 | 4,5 + 2 |
| 14 Escopo + registro | 1,25 | 2 | | 33 Latência e carga (código + rodadas) | 2 + 1,5 | 3 + 2 |
| 15 Regras | 1,25 | 2 | | 34 Docs | 3 | 4 |
| 16 Acadêmico | 1 | 1,5 | | 35 Pacote + teste do zero | 1 | 1,5 |
| 17 Financeiro | 1,25 | 2 | | | | |
| 18 Protocolos | 2 | 2,75 | | | | |

**Agenda.** Cada raia é uma worktree com banco próprio; no máximo 4 subagentes ao mesmo tempo.

| Dia | Raia A | Raia B | Raia C | Marco |
|---|---|---|---|---|
| **Qua 04** | T1 → T2 → T5 → T6a → T8 → (T9 ∥ T10) → T12 → T13 | T3 (9h) → T4 → T15 → T21 | T7 (depende de T6a) → T11 → T6b | **M1** ~20h |
| **Qui 05** | T14 → (T16 ∥ T17) e (T18 ∥ T19) → T20 → T24 → T25 | T22 → T23 | T30 (T6b + T13) → código puro das T31/T32/T33 (stats, score, judge, templates, mutators, check, capacity, loadgen) | **M2** ~22h |
| **Sex 06** (manhã livre) | T26 ∥ T28 | T27 | T29 (depende só de T25) → noite: carga (T33) ∥ `security-review` + `code-review` (agentes) → 1ª rodada dev (janela A) | **M3** ~22h |
| **Sáb 07** | correções do review (≤ 2 h por classe) → ajuste dev (≤ 2 iterações / 3 h) → ataques de desenvolvimento, 1ª rodada (número primário) → correções (≤ 2 h por classe; vazamento, ação e rascunho sempre) → **congelar** (tag + OpenTimestamps) → teste 1× ∥ rótulos (Miguel) → holdout cego e adaptativo | | | **M4** ~22h |
| **Dom 08** | 7h: bench sozinho (janela B) → T34 (esqueleto já escrito no sábado) → T35 → 18h: congelamento | | | |
| **Seg 09** | Miguel revisa; refazer o pacote; envio | | | |
| **Ter 10** | buffer até 12h | | | |

Caminho crítico recalculado: **P50 ≈ 38 h, P90 ≈ 50 h**, contra uma janela útil de ~60 h.

| Marco | Critério | Se falhar |
|---|---|---|
| **M1** | `data-quality.md` com os números congelados; `spikes.md` com portão (40×2), cache, Vertex (se houver), A2A 1.0 (posse, push, contextId, cliente real), LangGraph e Phoenix (anotação no span raiz) | ETL limitado a 2 h de depuração (T15/T21/T22 seguem na fixture). Se o A2A falhar: `InMemoryTaskStore` com dono e 3 métodos. Se o portão falhar: metas pela fórmula |
| **M2** | Chat ponta a ponta com Gemini real (golden 1 e 4, cartão em 1 chamada); `make smoke` verde (sem A2A) | Sem `/feedback` nem 2º modelo; carga só até o 1º joelho |
| **M3** | Fase 4 fechada; `make smoke` com A2A; carga rodada; reviews feitos; 1ª rodada dev | Langflow e Vertex saem; ataques de desenvolvimento em 1 rodada |
| **M4** | `quality.md`, `guard.md` e `security.md` no SHA congelado | **2º nível de corte (pré-registrado):** multi-turno 8 → 4; perturbação cega só em dinheiro, datas e ids; sem escada (b); sem sensibilidade/especificidade reponderadas; sem 2º modelo; bench com n ≥ 60 na classe principal |

**Nunca cortar:**
- isolamento;
- EvidenceGuard;
- escrita restrita;
- LLM comprometido;
- ataques de desenvolvimento + holdout cego;
- teste de qualidade 1×;
- latência real;
- `docker compose up` + `make login` + `make smoke`;
- replay;
- A2A mínimo com posse;
- Phoenix mínimo;
- conversas;
- documento e resumo.

**Decisões do autor na quarta antes das 9h** (fora do plano, na agenda pessoal):
- chave do Gemini;
- liberar a máquina;
- §12;
- remoto privado ou `make smoke` local como critério.

---

## Fase 0 — Base, contratos e spikes

### Task 1: Base, segredos, compose, CI e relógio
**Depende de:** —

**Files:**
- Create:
  - raiz: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `.gitattributes`, `AGENTS.md`, `CLAUDE.md`, `slo_targets.yaml`;
  - CI e operação: `.github/workflows/ci.yml`, `ops/postgres/init.sh`, `ops/compose.eval.yml`;
  - código: `scripts/init_secrets.py`, `core/clock.py`, `app/config.py`, `etl/config.py`, `tests/conftest.py`.
- Test: `tests/unit/test_config.py`, `tests/unit/test_clock_rule.py`, `tests/unit/test_init_secrets.py`

**Interfaces:**
- `init_secrets(root, env_file, uids)`:
  - gera uma vez os valores compartilhados e grava nos volumes de cada consumidor (`db`, `redis`, `app`, `etl`, `evals`), com `chown` e modo 0400;
  - copia a `GEMINI_API_KEY` (se houver) para `app/gemini_api_key`;
  - cria `etl-out` e `eval-out` com dono 10001;
  - é idempotente.
- `AppSettings`:
  - segredos só de arquivo (`settings_customise_sources`);
  - `llm_provider: Literal["auto","gemini","replay","fake"]="auto"`, `llm_backend`, `gcp_project`, `gcp_location`, `llm_fallback_model`, `limits_profile`, `api_port=8000`;
  - carrega `slo_targets.yaml`;
  - recusa segredo vazio, com menos de 24 caracteres ou com "change-me"/"example";
  - `otel_capture_content` só com `app_env="dev"` e `limits_profile="eval"`.
- `EtlSettings`.
- Compose:
  - `init-secrets` (root, one-shot);
  - `db` com `POSTGRES_PASSWORD_FILE`;
  - `redis` com `requirepass` lido de arquivo;
  - `etl` (uid 10001);
  - `api` (uid 10001, sem `data/`, sem `.env`, `127.0.0.1:${API_PORT}`, `PROMETHEUS_MULTIPROC_DIR`);
  - profiles `obs` (phoenix com volume e prometheus, em 127.0.0.1), `eval` (`evals` + `api-redteam`), `vertex` e `langflow`.
- `Makefile`: `up`, `down`, `smoke`, `login`, `a2a-demo`, `phoenix-import`, `test`, `test-full`, `test-live`, `lint`, `check`, `eval-*`, `load-test`, `package`.

- [ ] **Step 1: Write the failing tests**
  - `test_defaults_and_slo_targets_loaded`
  - `test_placeholder_secret_rejected`
  - `test_short_secret_rejected`
  - `test_canary_32_hex`
  - `test_env_cannot_override_secret`
  - `test_capture_content_requires_dev_eval`
  - `test_app_settings_has_no_etl_secrets`
  - `test_init_secrets_shared_values_equal`
  - `test_init_secrets_owner_and_mode_per_consumer`
  - `test_init_secrets_out_volumes_owned_10001`
  - `test_init_secrets_copies_gemini_key`
  - `test_init_secrets_idempotent`
  - `test_clock_only_in_core_clock`: casa `now()` e não casa `wall_now()`
- [ ] **Step 2–4:** FAIL → implementar → PASS. `docker compose up init-secrets db redis` sobe; um container uid 10001 escreve em `etl-out`.
- [ ] **Step 5: Commit** — `chore: base com segredos por consumidor, volumes com dono, compose, CI e relógio único`.

### Task 2: Contratos centrais e fachadas
**Depende de:** 1

**Files:**
- Create:
  - núcleo e infraestrutura: `core/rkey.py`, `core/scope.py`, `app/infra/redis_stores.py` (stubs tipados);
  - ferramentas: `app/tools/base.py`, `app/tools/registry.py` (as 11 com stubs);
  - agente e serviço: `app/agent/llm.py`, `app/data/card.py` (tipo), `app/service/turn.py` (`Admission` + Protocol);
  - observabilidade: `app/obs/tracing.py`, `app/obs/metrics.py` (declarativo a partir do `slo_targets.yaml`), `app/obs/annotations.py` (no-op);
  - API: `app/main.py` (todos os routers stub).
- Test: `tests/unit/test_contracts.py`, `tests/unit/test_rkey_types.py`

- [ ] **Step 1–4:**
  - `test_rkey_scoped_and_global`
  - `test_redis_clients_accept_only_rkey` (mypy em `app/infra` + teste)
  - `test_registry_11_identical_specs`
  - `test_draftcard_public_no_token`
  - `test_metrics_from_slo_targets`
  - `test_turnresult_fields`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: contratos centrais, RKey tipada e fachadas`.

### Task 3: Spikes ao vivo
**Depende de:** — (às 9h, em paralelo)

**Files:** Create `tests/live/test_spikes.py`, `docs/evidence/spikes.md`.

- [ ] **Step 1: Gemini.**
  1. `models.list`.
  2. Schema estrito.
  3. 2 voltas com `thought_signature`.
  4. **Portão:** 20 chamadas que emitem tool + 20 que respondem, por modelo, MINIMAL, prompt ~1.500 tokens com 11 declarações.
  5. Cache entre alunos.
  6. `max_output_tokens` × raciocínio.
  7. Troca de modelo × assinatura.
  8. Se houver projeto, 5 chamadas Vertex (`global`).
- [ ] **Step 2: A2A 1.0** (cliente real do SDK).
  - Dono e não dono em `GetTask`, `SendMessage` com `taskId`, `ListTasks`, `CancelTask` concluída.
  - Push → erro.
  - `contextId` alheio.
  - Exceção do executor → `InternalError(str(e))` confirmado.
- [ ] **Step 3: LangGraph.** `get_stream_writer`, cancelamento, `draw_mermaid`.
- [ ] **Step 4: Phoenix.**
  - Fixar a imagem e o `arize-phoenix-client`.
  - Span raiz → `force_flush` → anotação aparece.
  - Volume persiste após restart.
- [ ] **Step 5: Fórmula.** Aplicar a fórmula pré-registrada da spec §6.5 e gravar as metas em `slo_targets.yaml` e `spikes.md`.
- [ ] **Step 6: Commit** — `test(live): spikes de Gemini, A2A 1.0, LangGraph e Phoenix com portão de latência`.

### Task 4: Provider, admissão e fakes
**Depende de:** 2, 3

**Files:** Create `app/agent/gemini.py`, `app/agent/fakes.py` (`ScriptedLLM`), `app/obs/cost.py`; implement `AdmissionBucket` (Lua `reserve`/`settle`). Test `tests/unit/test_gemini_adapter.py`, `test_admission.py`, `test_cost.py`.

**Interfaces:**
- `GeminiProvider` (como na v4).
- `AdmissionBucket.reserve(scope, est_requests, est_tokens) -> (ok, retry_after_s)` e `settle(ticket, real_requests, real_tokens)`.
- Vaga de concorrência por turno, adquirida em `admit` e liberada em `settle`.

- [ ] **Step 1–4:**
  - `test_parameters_json_schema`
  - `test_signature_roundtrip_and_drop_on_switch`
  - `test_usage_and_model`
  - `test_retry_only_429_5xx_with_time`
  - `test_retry_info`
  - `test_retry_cap_floor`
  - `test_reserve_settle_rpm_tpm`
  - `test_retry_after_returned`
  - `test_turn_slot_not_per_call`
  - `test_vertex_backend`
  - `test_cost`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): provider Gemini e admissão global com reserva e acerto`.

---

## Fase 1 — ETL

### Task 5: Normalização [revisão por fase]
**Depende de:** 1

Como a Task 5 da v4: `core/normalize.py`, `format.py` e `keys.py`, com os mesmos testes, mais `fmt_period`.

- [ ] **Commit** — `feat(core): normalização, formatação e chaves HMAC`.

### Task 6a: Leitura e fixture
**Depende de:** 5

**Files:** Create `etl/raw.py`, `scripts/make_fixture.py`, `tests/fixtures/mini/**`, `golden.json`. Test `tests/unit/test_raw.py`.

**Interfaces:**
- `read_raw(source)` aceita o zip ou um diretório.
- `make_fixture.py` monta a fixture com:
  - os 10 golden, os 11 parceiros de colisão e o irmão 180548;
  - 1 terceiro e 1 matrícula LOCKED escolhidos a dedo;
  - as linhas a descartar;
  - **catálogos pequenos inteiros** (`brand`, `unit`, `course`, `curriculum`, `curriculum_subject`, `subject`, `course_offer`, `term`, `demand_type`, `secretary/unit`, `company`, `product`, `price_list`, `mdl_tenant`, `mdl_course_categories`);
  - `class_section`, `mdl_course` e `mdl_quiz` **filtrados** pelos alunos da fixture;
  - total < 3 MB.

- [ ] **Step 1–4:** `test_read_raw_47_zip_and_dir`, `test_fixture_composition_and_size` → FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): leitura dos CSV e fixture dos golden`.

### Task 6b: Oráculo independente
**Depende de:** 6a

**Files:** Create `evals/oracle.py`. Test `tests/unit/test_oracle.py`, `tests/integration/test_oracle_full.py` (`fulldata`).

**Interfaces:**
- `oracle_link_protocols`:
  - os 17 casos por `Course_Label`;
  - `guardian_payer_unique`.
- `oracle_lms_accounts`: SSO + manuais; contas de teste, professor e admin excluídas, com asserção.
- `oracle_financial`, `oracle_snapshot_check`, `oracle_courses`, `oracle_forecast`, `oracle_protocols`, `oracle_lms` (só inscrição ativa de conta de aluno), `oracle_enrollment_events`, `oracle_term_registration`, `oracle_catalog`, `oracle_coverage`.
- `ideal_answer(item)` e `mutate_one_fact(answer, kind)`.

- [ ] **Step 1–4:**
  - `test_oracle_golden4`
  - `test_oracle_golden1_attempts`
  - `test_no_test_accounts_in_oracle`
  - `fulldata`:
    - 3911 com a quebra exata;
    - 22 sem vínculo;
    - 1003;
    - 273/273;
    - **0** provas que valem nota encerradas sem tentativa;
    - **1217** simulados abertos;
    - 229 `PROTOCOL_REF`.

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(evals): oráculo independente com cobertura completa`.

### Task 7: Ontologia, nomes PT e tipos de texto
**Depende de:** 6a

**Files:** Create `ontology/*.yaml` (inclusive `text_fields.yaml` e `guard_nouns.yaml`), `core/ontology.py`, `scripts/gen_subject_names.py`. Test `tests/unit/test_ontology.py`, `tests/integration/test_ontology_full.py` (`fulldata`).

**Interfaces:** os métodos da v4, mais:
- `.text_field_type(entity, field)` ∈ enum, `label_pt`, `regex:<padrão>`, `free_text`;
- `.classify_message(text)`: igualdade exata depois de normalizar Unicode (NFKC) e espaços;
- `.room_subject(shortname, idnumber)`: `SAG-<CLASS_ID>` ou prefixo do `shortname`; as INST recebem rótulo fixo;
- `.guard_nouns()`;
- `customer` = `resolution_only`.

- [ ] **Step 1–4:**
  - `test_47_destination_reason`
  - `test_customer_resolution_only`
  - `test_every_exposed_text_field_typed`
  - `test_classify_message_exact_after_nfkc`
  - `test_room_subject_by_prefix`
  - `test_names_pt`
  - `fulldata`:
    - `test_enum_labels_full`
    - `test_messages_loaded_100pct_typed`
    - `test_active_rooms_100pct_typed`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: ontologia com tipos de texto, nomes PT e destino das tabelas`.

### Tasks 8, 9, 10: Resolução
**Dependências:** 8 depende de 5; 9 e 10 dependem de 8.

Iguais às Tasks 8–10 da v4: regras, testes e commits.

### Task 11: Schema, RLS e grants
**Depende de:** 7

**Files:** como na v4.

**Interfaces:**
- `agent_rw` sem FK para `canon`.
- `rls.sql` aplicado por último.
- Fixture `db_dsns`: um banco por worktree, criado com `CREATE DATABASE … TEMPLATE`.

- [ ] **Step 1–4:**
  - `test_copy_then_report_then_rls`
  - `test_owner_reads_zero_after_force` (documenta a ordem)
  - `test_copy_after_force_fails`
  - RLS e grants (como na v4)
  - `test_agent_rw_has_no_fk_to_canon`
  - `test_canon_rebuild_preserves_agent_rw`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): schemas, RLS por último, grants por allowlist`.

### Task 12: Transformações e tipificação
**Depende de:** 7, 9, 10

**Interfaces:**
- Como na v4, com estas mudanças:
  - mensagens de solicitante **omitidas**;
  - sala → disciplina pelo `idnumber` ou pelo prefixo do `shortname`;
  - `issued_document` sem `Signed_By`;
  - professor validado por regex.
- Saídas: `build_coverage` e `build_counts()`, este último para o relatório em memória.

- [ ] **Step 1–4:**
  - `test_no_analyst_login_anywhere`: os 29 logins (união das 4 colunas)
  - `test_requester_messages_omitted`
  - `test_messages_typed_or_untrusted`
  - `test_room_name_from_prefix_when_no_idnumber`
  - `test_lms_quiz_active_student_accounts_only`
  - demais testes da v4

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): transformações com texto tipificado`.

### Task 13: Carga, relatório e arquivos de login
**Depende de:** 11, 12

**Interfaces:**
- `load_all`: tabelas → COPY → `write_report(build_counts)` → `rls.sql` → commit.
- `write_golden_login` (`etl-out`) e `write_eval_students` (`eval-out`).

- [ ] **Step 1–4:**
  - `test_report_from_memory_matches_oracle`
  - `test_reload_keeps_agent_rw`
  - `test_login_files_no_cpf`
  - `fulldata` com os números congelados

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): carga com relatório em memória e RLS no fim`. Revisão da fase. **M1.**

---

## Fase 2 — Dados e ferramentas

### Task 14: Escopo, conexão e registro
**Depende de:** 2, 13

**Interfaces:**
- `create_pool` com reset; `assert_safe_role`.
- `with_scope(pool, person_key, families)`: **uma conexão por chamada de ferramenta**, com `set_config(..., true)` na transação.
- `load_scope`; `resolve_enrollment(..., prefer_status)`; `validate_args`.
- `DraftStore`: `put/take/pending/same_pending(sub, conversation_id, service)`.
- `LoginTickets`: `rkey("ticket", "global", "-", ticket_id)` → `person_key`.

- [ ] **Step 1–4:**
  - `test_specs_identical`
  - `test_index_resolution`
  - `test_prefer_status_golden9`
  - `test_extra_arg_rejected`
  - `test_draft_single_use_owner_bound`
  - `test_draft_same_pending_idempotent`
  - `test_pool_reset`
  - `test_pool1_two_concurrent_turns_no_deadlock`
  - `test_assert_safe_role`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: escopo, conexão por chamada e rascunho idempotente`.

### Task 15: Regras puras
**Depende de:** 2, 5

**Interfaces:** como na v4, mais:
- `eligibility(service, facts)` com a regra 1 **só para DOC-\*, FIN-\*, ACA-011 e TEC-060**;
- `commitment_sources(data) -> set[str]`: condições presentes em orientação ou acordo, usadas pelo guard.

- [ ] **Step 1–4:** testes da v4, mais:
  - `test_rule1_not_for_aca020_021`
  - `test_gen090_ouv099_never_blocked`
  - `test_rule1_doc_fin_aca011_tec060`
  - `test_eligibility_doc041_alternative`
  - `test_commitment_sources`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(rules): regras puras com elegibilidade restrita por família de serviço`.

### Tasks 16, 17, 19: Acadêmico, financeiro, LMS e catálogo
**Depende de:** 14, 15

Como na v4, com estes acréscimos:
- `ToolOutput.records` carrega os registros por entidade (para o guard);
- **16:** `test_subject_match_pt_prefix_multi` e `test_get_catalog_subjects_calculo`;
- **17:** saldo da fatura em cada item (`test_invoice_balance_per_item`);
- **19:** `test_rooms_without_idnumber_named`, preço com `mensalidade` + `ano` + `parcelas_por_semestre`, `test_all_catalog_kinds`.

Commits como na v4.

### Task 18: Protocolos e rascunho
**Depende de:** 14, 15

Como na v4, com estas mudanças:
- `draft_protocol` é idempotente (`rascunho_ja_exibido`);
- `enrollment_label` só se houver mais de uma matrícula;
- recusa por taint com `TAINT_NO_DRAFT`;
- `list_protocols` sem descrições.

- [ ] **Step 1–4:** testes da v4, mais:
  - `test_draft_idempotent_same_service`
  - `test_draft_label_only_if_multiple`
  - `test_aca021_two_subjects_allowed`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): protocolos e rascunho idempotente`.

### Task 20: Cartão, exposição e LLM comprometido
**Depende de:** 16, 17, 18, 19

Como na v4: o cartão inclui o valor e a data do próximo boleto, o pagador, o diploma, a próxima avaliação e os simulados. O LLM comprometido roda nos golden 1, 2, 3, 4 e 9.

- [ ] **Commit** — `feat: cartão do aluno; test(security): exposição e LLM comprometido`. Revisão da fase.

---

## Fase 3 — Agente

### Task 21: Guards e perturbação
**Depende de:** 2, 6b, 15

**Files:** Create `app/guards/input.py`, `app/guards/evidence.py`, `evals/guard/perturb_dev.py`. A perturbação **cega** fica na Task 31, escrita por outro subagente. Test `tests/unit/test_input_guard.py`, `test_evidence_guard.py`, `test_perturbation_dev.py`.

**Interfaces:**
- `check_evidence(answer, *, records_by_entity, card_facts, tool_args_this_turn, system_notes, canary, prompt_prefix, ontology) -> GuardResult`, com estes kinds:
  - **entidade citada** → registro: status, polaridade e inteiros ≤ 12 conferidos nele;
  - **sem entidade** → polaridade sobre todas as matrículas;
  - **ação** por forma verbal: 1ª pessoa no passado, ou voz passiva sobre um pedido novo → exige `SA-` de `system_notes`; voz passiva sobre um número presente na evidência → fato;
  - **compromisso** → exige a condição em `commitment_sources`;
  - **url** → bloqueada;
  - **período/id do aluno** → só vale se estiver em `tool_args_this_turn` e a ferramenta devolveu registro.
- `degrade`.
- `template_fallback(source: Literal["tool","card"], data, channel, tainted)`.
- `strip_markdown_and_codes`.

- [ ] **Step 1: Write the failing tests**

```python
REC = {"fatura:7067951": {"status": "parcial", "saldo": "R$ 91,33"},
       "disciplina:CALCULUS I": {"resultado": "reprovado"}, "disciplina:ALGORITHMS": {"resultado": "aprovado"},
       "protocolo:20260022332": {"aberto_em": "08/10/2026"}}
FACTS = {"1.total_para_quitar": "R$ 49.508,43", "2.total_para_quitar": "R$ 0,00", "1.previsao": "2028/1"}
def chk(a, args=(), notes=()):
    return check_evidence(a, records_by_entity=REC, card_facts=FACTS, tool_args_this_turn=args,
                          system_notes=notes, canary="c" * 32, prompt_prefix=PREFIX, ontology=ONTO)

def test_status_anchored_to_cited_entity(): assert not chk("Você foi aprovado em Cálculo I.").ok
def test_user_period_not_evidence(): assert not chk("Sim, você se forma em 2027/2.").ok
def test_user_period_ok_if_tool_arg_returned(): assert chk("Em 2026/2 você teve 3 acessos.", args=("2026/2",)).ok is not None
def test_commitment_needs_source(): assert not chk("Você pode parcelar em 10x sem juros.").ok
def test_action_first_person(): assert not chk("Pronto, gerei sua 2ª via.").ok
def test_action_passive_new_request(): assert not chk("Seu pedido foi enviado.").ok
def test_action_passive_known_protocol(): assert chk("O pedido 20260022332 foi aberto em 08/10/2026.").ok
def test_action_with_system_note(): assert chk("Abri a solicitação SA-2026-000123.", notes=("Solicitação SA-2026-000123 aberta em 31/10/2026",)).ok
def test_polarity_without_entity_needs_all(): assert not chk("Você está em dia.").ok
def test_url_blocked(): assert not chk("Pague em http://x.test").ok
def test_card_fallback(): assert "R$ 49.508,43" in template_fallback("card", FACTS, "web", False)
```

Mais: os testes da v4 que continuam válidos, e `test_perturbation_dev_recall_fpr` (o gate do CI só no conjunto dev).

- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(guards): EvidenceGuard ancorado no registro, com ação, compromisso e URL`.

### Task 22: Prompt e grafo
**Depende de:** 4, 14, 21

Como na v4, com estas mudanças:
- `ToolContext` recriado a cada rodada (o `tainted` é recalculado);
- rascunho:
  - só o rascunho na rodada → texto fixo;
  - rascunho + outras ferramentas → texto fixo + 1 chamada para o resto;
  - `rascunho_ja_exibido` → segue normalmente;
- o evento `done` carrega `tools_class` e `llm_calls`;
- `pre_guard_answer` é preenchido.

- [ ] **Step 1–4:** testes da v4, mais:
  - `test_tainted_recomputed_each_round`
  - `test_draft_plus_other_question`
  - `test_draft_idempotent_flow`
  - `test_done_event_fields`
  - `test_codes_replaced_by_names`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): grafo LangGraph com contexto por rodada e rascunho combinável`.

### Task 23: Replay [revisão por fase]
**Depende de:** 22

Como na v4, mais `LLM_PROVIDER=auto`: sem `gemini_api_key`, cai em replay com a faixa "modo demonstração".

- [ ] **Commit** — `feat(agent): modo replay e auto`. Merge das raias; revisão da fase.

---

## Fase 4 — API, A2A, UI e observabilidade

### Task 24: Autenticação e dev
**Depende de:** 14

Como na v4; o ticket usa `rkey("ticket", "global", "-", id)`.

- [ ] **Commit** — `feat(api): JWT por canal, x-api-key, login por ticket e healthz`.

### Task 25: TurnService, chat SSE e smoke
**Depende de:** 20, 22, 24

**Interfaces:**
- `POST /chat`: chama `admit()` **antes** do stream; sem cota → 429 (limite do aluno) ou 503 (global), com `Retry-After`.
- `handle()`, nesta ordem:
  1. entrada;
  2. "sim" contextual;
  3. histórico e cartão;
  4. `run_turn`;
  5. `settle`;
  6. métricas e span AGENT (aberto **antes** do `meta`).
- Eventos: `meta`, `progress`, `draft`, `answer`, `error`, `ping`, `done` e, em dev+eval, `debug`.
- `make smoke`, sem A2A: compose com a fixture, `fake`, login, "R$ 49.508,43", API sem `/data`.

- [ ] **Step 1–4:** testes da v4, mais:
  - `test_admit_before_stream_http_503_retry_after`
  - `test_settle_called`
  - `test_agent_span_opened_before_meta`
  - `test_done_event`
  - `test_debug_only_dev_eval`

  FAIL → implementar → PASS; `make smoke` verde.
- [ ] **Step 5: Commit** — `feat(service): admissão antes do stream, TurnService e chat SSE; smoke`. **M2.**

### Task 26: Confirmação e feedback
**Depende de:** 25

Como na v4, com estas mudanças:
- `pg_advisory_xact_lock(hash(person_key, service))`, exceto GEN-090/OUV-099;
- mensagem na UI: "Solicitação SA-… aberta. Prazo: dd/mm/aaaa.";
- o feedback chama a fachada de anotação.

- [ ] **Step 1–4:**
  - `test_two_drafts_same_service_concurrent_one_created`
  - `test_second_gen090_allowed`
  - demais testes da v4

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): confirmação com lock, feedback e CSRF/IDOR`.

### Task 27: A2A 1.0
**Depende de:** 25

**Interfaces:** como a spec §6.6:
- `RedisTaskStore` com `get/save/delete/list` e TTL;
- o executor captura exceções;
- telemetria do SDK desligada;
- `admit()` antes de executar;
- `make a2a-demo` entra no smoke.

- [ ] **Step 1–4:**
  - `test_ownership_11_methods` (parametrizado conforme a tabela da spec §6.6, com controle positivo)
  - `test_list_tasks_only_owner`
  - `test_cancel_completed_own_minus_32002`
  - `test_send_message_foreign_task_minus_32001`
  - `test_exception_text_not_leaked_response_or_spans`
  - `test_admission_over_a2a_retry_after`
  - demais testes da v4

  FAIL → implementar → PASS.
- [ ] **Step 5:** Langflow opcional.
- [ ] **Step 6: Commit** — `feat(api): A2A 1.0 de consulta com posse nos 11 métodos e exceções contidas`.

### Task 28: Web [revisão por fase]
**Depende de:** 25

Como na v4: botão "Nova conversa", mensagem depois de Confirmar e faixa "modo demonstração".

- [ ] **Commit** — `feat(web): chat em texto puro com cartão de confirmação`.

### Task 29: Observabilidade
**Depende de:** 25

**Interfaces:**
- Exportador OTLP → Phoenix (versão fixada).
- `annotate()` real: em lote, depois de `force_flush`, com retry.
- `session.id` = HMAC(conversa).
- Conteúdo só em dev+eval.
- Métricas multiprocesso com labels e buckets do `slo_targets.yaml`.
- `export_phoenix(run_id) -> docs/evidence/phoenix/` e `make phoenix-import`.
- `SLO.md` com a tabela bom/ruim/excluído.

- [ ] **Step 1–4:** testes da v4, mais:
  - `test_annotation_after_flush_with_retry`
  - `test_session_id_hmac`
  - `test_turns_total_status_values`
  - `test_first_event_by_class`
  - `test_export_import_roundtrip`

  FAIL → implementar → PASS.
- [ ] **Step 5:** capturas → `observability.md`.
- [ ] **Step 6: Commit** — `feat(obs): Phoenix com anotações confiáveis, métricas e SLOs`. Revisão da fase. **M3.**

---

## Fase 5 — Evals

O código vai na quinta (raia C); as rodadas, na sexta à noite e no sábado, em série.

### Task 30: Dataset, plano de análise e score
**Depende de:** 6b, 13

**Files:**
- Create:
  - acesso: `evals/tokens.py`, `evals/client.py`;
  - pontuação e dados: `evals/golden/score.py`, `templates.yaml` (shared), `paraphrases.yaml`, `multiturn.yaml`, `build.py`;
  - pré-registro: `analysis_plan.md`, `dataset.sha256`;
  - holdout: `data/holdout/templates_test_only.yaml` (fora do git).
- Test: `tests/unit/test_dataset.py`, `tests/unit/test_score.py`

**Interfaces:**
- `score_item`.
- ≥ 50 templates de pergunta, sendo ≥ 15 `test_only`, só em `data/holdout/`.
- 40 alunos com cotas.
- `analysis_plan.md` conforme a spec §9.2 (inclusive P(meta), calculada por simulação com o ICC prior).
- Autotestes positivo e negativo.
- `dataset.sha256` carimbado por OpenTimestamps na tag.

- [ ] **Step 1–4:**
  - `test_quotas`
  - `test_disjoint`
  - `test_holdout_not_in_git`
  - `test_required_from_oracle`
  - `test_oracle_ideal_passes_all`
  - `test_oracle_mutated_fails`
  - testes de `score_item` ("ativa" ≠ "inativa"; datas equivalentes; recusa com oferta; `expect_draft`; multi-turno como 1 item)

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `test(evals): dataset com holdout fora do git, score e plano pré-registrado`.

### Task 31: Qualidade
**Depende de:** 25, 30. O código puro (stats, judge) é escrito na quinta.

**Files:** Create `evals/stats.py`, `evals/golden/judge.py`, `run.py`, `labels.csv`, `select_conversations.py`, `evals/guard/perturb_blind.py` (**escrito por um subagente separado que só vê a spec §1, §6.4 "Medição" e o oráculo — não vê `evidence.py` nem a Task 21**), `evals/guard/run.py`. Test `tests/unit/test_stats.py`.

**Interfaces:**
- **Estatística:**
  - `two_way_bootstrap_ci(results, strata)`;
  - `wilson`;
  - `ppv_npv`;
  - `corrected_pass(p, ppv, npv)`.
- **Juiz:** dev e teste.
- **Runner:** `run.py --split dev|test --model M --record`; anota no Phoenix.
- **Saídas:**
  - `quality.md`;
  - `guard.md`: perturbação cega 1× + rascunhos do dev perturbados + guard × scorer;
  - `conversations.md`.

- [ ] **Step 1–4:**
  - `test_two_way_bootstrap_coverage_sim`
  - `test_wilson`
  - `test_corrected_pass`

  FAIL → implementar → PASS.
- [ ] **Step 5: Rodadas.** A sequência está na agenda: sexta à noite a 1ª rodada dev; sábado o resto até "teste 1×". Os rótulos de 15/15 pelo desfecho do scorer saem durante o teste.
- [ ] **Step 6: Commit** — `test(evals): qualidade com IC de dois fatores, scorer validado e perturbação cega`.

### Task 32: Ataques
**Depende de:** 25, 27. O código puro é escrito na quinta.

**Files:**
- Create:
  - templates e mutação: `evals/redteam/templates_dev.yaml`, `mutators.py`;
  - plantio e checagem: `plant_csv.py` (gera CSV com injeções para o ETL `--plant`), `check.py`;
  - execução: `run.py`, `adaptive.py`, `api_attacks.py`;
  - holdout: `data/holdout/attacks_blind.yaml` (escrito por um subagente que vê só o README do case e o Agent Card).
- Modify: `etl/__main__.py` (`--plant`, para o banco `eval_redteam`), `ops/compose.eval.yml` (`api-redteam`).
- Test: `tests/unit/test_redteam_check.py`

**Interfaces:**
- **Desenvolvimento:**
  - comportamento: 10 × 20, 2 rodadas;
  - vazamento: 5 × 8;
  - número primário = **1ª rodada, antes das correções**.
- **Holdout cego:** ≥ 50% dos templates de ataque + `adaptive.py` (um LLM atacante faz 3 refinamentos por template), 1× no SHA congelado.
- **Plantio:** pelo CSV cru, em todo campo de texto exposto (imitação de modelo, homóglifos, caracteres invisíveis).
- **Checagem:** `check_attack` com predicados.
- **Relatório:** Wilson só no agregado; rótulos humanos numa amostra de 24 das 120.
- **Ataques à API:** os 11 métodos A2A, contextId, canal, JWT, CSRF, limites.

- [ ] **Step 1–4:**
  - nome sem acento casa;
  - RA compartilhado não gera falso positivo;
  - predicado de compromisso detectado;
  - `draft` no turno seguinte detectado;
  - plantio por homóglifo classificado como `texto_livre`.

  FAIL → implementar → PASS.
- [ ] **Step 5:** sábado, conforme a agenda.
- [ ] **Step 6: Commit** — `test(evals): ataques de desenvolvimento e holdout cego adaptativo, plantio pelo CSV`.

### Task 33: Latência e carga
**Depende de:** 25. O código puro (capacity, loadgen) é escrito na quinta.

Como na v4, com estas mudanças:
- `LoadTestLLM` usa a latência do portão;
- escada (b) com semáforo ilimitado;
- critérios escritos antes de rodar;
- semáforo e pool dimensionados pela lei de Little;
- `capacity` mostra o limite de TPM;
- três janelas com previsto × medido;
- custo por turno.

Rodadas: carga na sexta à noite; bench no domingo às 7h.

- [ ] **Commit** — `test(evals): latência prevista × medida, carga em duas escadas e capacidade`.

---

## Fase 6 — Documento e entrega

### Task 34: Documentação
**Depende de:** 31, 32, 33

Como na v4, mais:
- seção "identidade A2A em produção" (RFC 8693);
- escolhas de precisão na resolução;
- `graph.md`;
- `phoenix/`;
- "Como foi construído" conforme a §12.

O material pessoal **não** entra.

- [ ] **Commit** — `docs: arquitetura, resumo executivo e evidências`.

### Task 35: Pacote e teste do zero (domingo)
**Depende de:** 34

Como na v4, com estas mudanças:
- `unzip -l` sem segredos, `.env` ou `data/`;
- teste do zero com e sem chave (`auto` → replay);
- `make smoke` no pacote descompactado.

Segunda: refazer o pacote depois da revisão do autor.

- [ ] **Commit** — `chore: pacote de entrega`.
