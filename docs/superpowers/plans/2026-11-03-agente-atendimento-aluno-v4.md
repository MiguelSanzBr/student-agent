# Agente de atendimento ao aluno — plano de implementação (v4, congelado)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agente que atende o aluno autenticado com isolamento por construção, respostas presas à evidência e evidências medidas de qualidade, segurança, latência e observabilidade.

**Architecture:** O ETL resolve as entidades dos 4 sistemas e grava num Postgres canônico com RLS forçado (o RLS é aplicado depois da carga), sem dado interno e com o texto livre tipificado. Uma FastAPI stateless lê a identidade só do JWT. Um `TurnService`, comum ao chat SSE e ao A2A 1.0, admite o turno contra a cota global, monta o cartão do aluno e roda um `StateGraph` do LangGraph sobre o `google-genai`. As ferramentas são tipadas e não recebem identidade. Toda resposta passa por um EvidenceGuard determinístico.

**Tech Stack:** Python 3.12, uv, FastAPI, psycopg 3 + psycopg_pool, Postgres 17, Redis 7, `google-genai` 2.34, `langgraph` 1.2, `a2a-sdk[http-server]` 1.3, `jsonschema`, `pyjwt`, `structlog`, `opentelemetry-sdk` + `openinference-semantic-conventions` + `arize-phoenix-otel`, `prometheus-client`, pytest (+ pytest-asyncio, fakeredis, hypothesis), ruff, mypy.

**Spec:** [docs/superpowers/specs/2026-11-03-agente-atendimento-aluno-design.md](../specs/2026-11-03-agente-atendimento-aluno-design.md) (v4). Pesquisa: [docs/research/2026-11-03-perfil-dos-dados.md](../../research/2026-11-03-perfil-dos-dados.md). Case: `data/case/README.md`, fora do git.

## Global Constraints

- **Commits:** autor `Miguel Sanz <miguelsanzb.p@gmail.com>` (configuração local), sem trailers. Conventional Commits em PT-BR.
- **Código e dados:** nenhum código de outro projeto; `data/` e segredos fora do git.
- **Idioma:** identificadores em inglês. Prompt, respostas, chaves de dados lidas pelo LLM e docs em PT-BR.
- **Relógio:** só em `core/clock.py`, com `reference_today(settings) -> date` (2026-10-31) e `wall_now() -> datetime`. O teste usa a regex `(?<![\w.])now\(|CURRENT_DATE|CURRENT_TIMESTAMP|date\.today|datetime\.now|datetime\.utcnow|time\.time\(` sobre `core/`, `app/`, `etl/`, `evals/` (exceto `evals/latency/`) e `*.sql`, e isenta só `core/clock.py`.
- **Chaves Redis:** todas vêm de `core.rkey.rkey(kind, sub, channel, id)`. Um teste proíbe f-string, `%` ou `+` montando chave Redis fora desse módulo.
- **Ferramentas:**
  - as 11 são **sempre declaradas**, sem parâmetro de identidade;
  - `enrollment` ∈ {"1","2","3"};
  - schema com `additionalProperties:false`, passado via `parameters_json_schema`;
  - a recusa por canal ou por taint acontece **na execução**;
  - ≤ 20 itens + `total`;
  - vazio → "Nenhum registro encontrado.".
- **Formatos:** `R$ 1.234,56`; `dd/mm/aaaa`; nota com 1 casa decimal.
- **Limites (perfil `default`):**

  | Item | Valor |
  |---|---|
  | Entrada | ≤ 2000 caracteres |
  | Mensagens | 20/min por aluno |
  | Tokens | 200.000/dia por aluno |
  | Teto global | US$ 20/dia (a 80%: sem retry do guard e ≤ 2 rodadas; a 100%: 503) |
  | Histórico | 5 turnos, TTL 1800 s; saídas de ferramenta com mais de 300 tokens são compactadas |
  | JWT | HS256, 30 min, `aud` ∈ {chat, a2a} |
  | Rascunho | 600 s |
  | Ticket de login | 300 s |
  | Grafo | ≤ 4 rodadas; orçamento de 20 s; `recursion_limit = 2·rounds + 6` |
  | LLM | `llm_timeout_s=8`; retry ≤ 10% com piso de 3/min |
  | Admissão | balde RPM+TPM global |

  O perfil `eval` tem limites próprios, não degrada o modelo e conta turno bloqueado como inválido.
- **Banco:**
  - schemas `canon` (recriado) e `agent_rw` (persistente);
  - roles `etl_owner` NOSUPERUSER e `agent_app` NOBYPASSRLS, com grants por allowlist (`etl/grants.yaml`);
  - a carga roda numa transação: tabelas → COPY → RLS/grants;
  - a API aborta se a própria role for privilegiada ou se algum segredo for inválido;
  - a API não recebe segredos do ETL nem monta `data/`.
- **Segredos:**
  - `init-secrets` gera cada valor compartilhado uma vez e faz `chown` por consumidor (`db` → 999, `app`/`etl` → 10001), com modo 0400;
  - os settings leem segredos **só** de arquivo (env e .env desligados para esses campos);
  - o canário tem 32 caracteres hexadecimais.
- **Escrita:** `AGENT_SERVICES = {DOC-001, DOC-002, DOC-003, DOC-040, DOC-041, ACA-011, ACA-020, ACA-021, FIN-030, FIN-031, FIN-032, TEC-060, GEN-090, OUV-099}`, com elegibilidade (inclusive protocolo aberto). Só pelo canal web, sem taint. O token nunca entra no contexto do LLM. O texto do turno de rascunho é fixo.
- **A2A 1.0:** `enable_v0_3_compat=False`. Os testes usam `SendMessage`, `GetTask`, `CancelTask`, `SubscribeToTask`, `ListTasks` e `CreateTaskPushNotificationConfig`. Item "não encontrado" deve resultar em `TaskNotFoundError` (-32001), com controle positivo.
- **Lint:** ruff (linha 100); `mypy --strict` em `core/`, `etl/`, `app/guards/` e `app/rules/`.
- **Revisão:**
  - por tarefa nas tarefas 1, 2, 11, 12, 13, 14, 18, 20, 21, 22, 24, 25, 26, 27 e 29;
  - por fase nas demais;
  - modelo mais barato só nas tarefas 5, 7 e 23;
  - `ponytail-review` e `pytest-coverage` ao fim de cada fase;
  - `security-review` no sábado de manhã (Task 31, passo 1), antes da rodada de teste.
- **Skills:** sempre `superpowers:test-driven-development` e `superpowers:verification-before-completion`; `fastapi-expert` nas tarefas 24–29.
- **Congelamento de comportamento no M4.** Depois dele, qualquer correção em `app/agent`, `app/guards`, `app/tools` ou `app/rules` exige refazer 1 rodada de ataques e os itens de qualidade afetados, regravar o replay e declarar o SHA e o delta nas evidências.

## Review Focus

1. **Aluno com matrícula ativa e formada** (golden 9: diploma emitido na matrícula 2) pergunta sobre diploma: a resposta usa a matrícula certa e cita o diploma emitido. Tasks 18, 20, 22.
2. **Fluxo "ver provas → pedir 2ª chamada"** funciona sem taint, porque o texto foi tipificado; texto plantado bloqueia o pedido. Tasks 12, 22.
3. **Banca roda depois de 10/11:** a resposta é a mesma. Tasks 1, 15, 17.
4. **Gemini com 429/5xx/timeout ou cota global esgotada:** 503 com `Retry-After` na admissão; durante o turno, retry só se houver tempo e depois evento `error` em PT-BR. Vale também via A2A. Tasks 4, 25, 27.
5. **Pergunta fora da cobertura, domínio vazio ou disciplina fora da matriz:** a mensagem certa para cada caso, nunca "0". Tasks 16, 17, 19, 22.

---

## Mapa de arquivos

| Caminho | Responsabilidade |
|---|---|
| raiz, `.github/`, `ops/` | projeto, compose, CI, segredos, roles, scrape, Langflow opcional |
| `core/` | `normalize`, `format`, `keys`, `clock`, `rkey`, `ontology`, `scope` |
| `ontology/` | `entities`, `enums`, `tables`, `free_text_templates`, `tools/*.yaml`, `subject_names_pt` |
| `etl/` | `config`, `raw`, `resolve_*`, `build`, `schema.sql`, `rls.sql`, `grants.yaml`, `load`, `report`, `__main__` |
| `app/config.py`, `app/contracts` (re-export) | settings e tipos |
| `app/data/` | `db`, `card`, `queries/*` |
| `app/rules/` | `finance`, `academic`, `lms`, `protocols`, `eligibility`, `coverage` |
| `app/tools/` | `base`, `registry`, domínios |
| `app/infra/redis_stores.py` | admissão, rate limit, orçamento, teto, histórico, rascunhos, tickets, TaskStore A2A |
| `app/agent/` | `llm`, `gemini`, `fakes`, `replay`, `prompt`, `messages`, `graph` |
| `app/guards/` | `input`, `evidence` |
| `app/service/turn.py` | TurnService |
| `app/api/` | `auth`, `dev`, `chat`, `protocols`, `feedback`, `a2a`, `health`; `app/main.py` |
| `app/obs/` | `logging`, `tracing`, `metrics`, `cost`, `annotations` |
| `app/web/` | chat |
| `scripts/` | `init_secrets`, `make_fixture`, `gen_subject_names`, `login_url`, `a2a_demo`, `package` |
| `evals/` | `oracle`, `tokens`, `client`, `stats`, `golden/`, `guard/`, `redteam/`, `latency/`, `replays/` |
| `tests/` | `unit`, `integration`, `e2e`, `live`, `fixtures/mini`, `adversarial.py` |
| `docs/` | `ARCHITECTURE.md`, `SLO.md`, `evidence/` |

## Interfaces centrais (criadas na Task 2)

```python
# core/rkey.py
def rkey(kind: str, sub: UUID, channel: Literal["web", "a2a", "-"], id: str) -> str: ...

# core/scope.py
@dataclass(frozen=True)
class EnrollmentRef:
    index: str; enrollment_key: UUID; label: str; brand: str; brand_family: str
    status: str; is_primary: bool
@dataclass(frozen=True)
class StudentScope:
    person_key: UUID; first_name: str
    enrollments: tuple[EnrollmentRef, ...]; brand_families: tuple[str, ...]

# app/data/card.py
@dataclass(frozen=True)
class StudentCard: text: str; facts: dict[str, str]   # chaves "<índice>.<campo>" e "geral.<campo>"

# app/tools/base.py
@dataclass(frozen=True)
class ToolSpec: name: str; description: str; parameters: dict[str, Any]
@dataclass(frozen=True)
class DraftCard:
    draft_id: str; token: str; service_code: str; service_name_pt: str; enrollment_label: str
    fee: str; due_date: str; description: str; warning: str
    def public(self) -> dict[str, str]: ...       # sem token, para o SSE; nunca ao LLM
@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, Any]; untrusted: bool = False; draft: DraftCard | None = None
@dataclass(frozen=True)
class ToolContext:
    scope: StudentScope; settings: AppSettings; channel: Literal["web", "a2a"]
    conversation_id: str; tainted: bool
    db: Callable[[], AbstractAsyncContextManager[AsyncConnection]]; drafts: DraftStore
Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutput]]
@dataclass(frozen=True)
class Tool:
    spec: ToolSpec; handler: Handler; read_only: bool = True
    allowed: Callable[[ToolContext], str | None] = lambda ctx: None   # motivo de recusa ou None

# app/agent/llm.py
@dataclass(frozen=True)
class ToolCall: id: str; name: str; args: dict[str, Any]; provider_meta: bytes | None = None
@dataclass(frozen=True)
class Usage: input_tokens: int = 0; output_tokens: int = 0; thinking_tokens: int = 0; cached_tokens: int = 0
@dataclass(frozen=True)
class LLMResponse:
    text: str | None; tool_calls: tuple[ToolCall, ...]; usage: Usage; model: str
    finish_reason: str = "STOP"; provider_meta: bytes | None = None
@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool", "system_note"]
    text: str | None = None; tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None; tool_name: str | None = None
    tool_result: dict[str, Any] | None = None; untrusted: bool = False
    provider_meta: bytes | None = None; model: str | None = None; turn: int = 0
class LLMProvider(Protocol):
    async def generate(self, *, model: str, system: str, messages: Sequence[Message],
                       tools: Sequence[ToolSpec], deadline: float) -> LLMResponse: ...
class LLMUnavailable(Exception): reason: Literal["timeout", "quota", "queue", "server"]

# app/agent/graph.py
@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: Literal["ok", "degraded", "fallback", "budget_exceeded", "llm_unavailable", "blocked_limit", "fixed"]
    guard: Literal["pass", "retry_pass", "degraded", "fallback", "n/a"]
    draft: DraftCard | None; tool_calls: tuple[str, ...]; llm_calls: int; model: str
    usage: Usage; timings_ms: dict[str, float]; first_event_ms: float | None
    pre_guard_answer: str | None; new_messages: tuple[Message, ...]

# app/service/turn.py
class TurnService:
    async def handle(self, *, scope: StudentScope, conversation_id: str, text: str,
                     channel: Literal["web", "a2a"], emit: Callable[[dict], Awaitable[None]]) -> TurnResult: ...

# app/obs/tracing.py (fachada; exportadores na Task 29)
def span(name: str, kind: Literal["AGENT", "LLM", "TOOL", "GUARDRAIL", "CHAIN"], **attrs) -> ContextManager[Span]: ...
# app/obs/metrics.py (declarativo; todas as métricas da spec §9.4 definidas aqui)
```

## Cronograma, estimativas e marcos

**P50 / P90 (horas de relógio, implementador + revisor):**

| Task | P50 | P90 | | Task | P50 | P90 |
|---|---|---|---|---|---|---|
| 1 Base | 1,5 | 2,5 | | 19 LMS + catálogo | 1,25 | 2 |
| 2 Contratos | 1 | 1,5 | | 20 Cartão + exposição + LLM comprometido | 1,5 | 2,5 |
| 3 Spikes | 1,5 | 2,5 | | 21 Guards + perturbação | 2,5 | 3,5 |
| 4 Provider | 2,5 | 3,5 | | 22 Prompt + grafo | 3 | 4,5 |
| 5 Normalização | 0,5 | 0,75 | | 23 Replay | 0,5 | 0,75 |
| 6 Raw + fixture + oráculo | 3 | 4 | | 24 Auth + dev | 1,25 | 2 |
| 7 Ontologia | 1,5 | 2,5 | | 25 TurnService + SSE + `compose-smoke` | 2,5 | 3,5 |
| 8 Resolução core | 0,75 | 1 | | 26 Confirmação + feedback | 1 | 1,5 |
| 9 Resolução LMS | 0,75 | 1 | | 27 A2A | 2,5 | 4 |
| 10 Resolução secretaria | 1 | 1,5 | | 28 Web | 0,75 | 1 |
| 11 Schema + RLS + grants | 1 | 1,5 | | 29 Observabilidade | 2 | 3 |
| 12 Build | 2 | 3 | | 30 Dataset + análise | 3 | 4 |
| 13 Carga + relatório | 1,25 | 2 | | 31 Qualidade (código + rodadas) | 3 + 2,5 | 4,5 + 3,5 |
| 14 Escopo + registro | 1,25 | 2 | | 32 Ataques (código + rodada) | 3 + 1 | 4 + 1,5 |
| 15 Regras | 1,25 | 2 | | 33 Latência e carga (código + rodadas) | 2 + 1,5 | 3 + 2 |
| 16 Acadêmico | 1 | 1,5 | | 34 Docs | 3 | 4 |
| 17 Financeiro | 1,25 | 2 | | 35 Pacote + teste do zero | 1 | 1,5 |
| 18 Protocolos | 1,75 | 2,5 | | | | |

**Agenda por dia** (raias = subagentes em paralelo; worktrees só nas raias marcadas):

| Dia | Raia A | Raia B | Raia C | Marco |
|---|---|---|---|---|
| **Qua 04** (9h–24h) | T1 → T2 → T5 → T8 → (T9 ∥ T10) → T12 → T13 | T3 (9h, sem depender de T1) → T4 | T6 → T7 → T11 | **M1 22h** |
| **Qui 05** | T14 ∥ T15 → (T16 ∥ T17) e (T18 ∥ T19) em worktrees → T20 | T21 → T22 → T23 | T30 (só precisa de T6 e T13) | **M2 22h** |
| **Sex 06** (manhã livre) | T24 → T25 → (T26 ∥ T28) | T27 | T29 → T31/T32/T33 (código) → carga da T33 (noite) | **M3 22h** |
| **Sáb 07** | T31.1 `security-review` (manhã) → rodada dev → ajuste (≤ 3 h) → T32 (ataques + correções) → **congelar** → teste 1× (T31) → rótulos (Miguel) | | | **M4 22h** |
| **Dom 08** | 7h: bench sozinho (T33) → T34 (esqueleto escrito no sábado à noite) → T35 → congelamento 18h | | | |
| **Seg 09** | Miguel: reescrita, vídeo, ensaio; refazer o pacote (+0,25 h); envio | | | |
| **Ter 10** | buffer até 12h | | | |

Caminho crítico estimado: **P50 ≈ 36 h, P90 ≈ 48 h**, contra uma janela útil de ~60 h (qua 9h → dom 18h, sem a sexta de manhã).

**Marcos e ação de corte:**

| Marco | Critério | Se falhar |
|---|---|---|
| M1 | `data-quality.md` com igualdade por regra (3911, 1003, 273/273); `spikes.md` com portão, cache, A2A 1.0 (posse, push) e stream do LangGraph | Depuração do ETL limitada a 2 h; as Tasks 15, 21 e 22 seguem na fixture. Se o spike do A2A falhar, o A2A vira `SendMessage` + `GetTask` com `InMemoryTaskStore` com dono. Se o portão disparar, as metas são reescritas pela fórmula |
| M2 | Chat ponta a ponta com Gemini real para os golden 1 e 4 (cartão em 1 chamada); `compose-smoke` verde | Sem `/feedback` nem 2º modelo; a carga vai só até o 1º joelho |
| M3 | Fase 4 fechada; carga da T33 rodada | Langflow e Vertex saem; ataques em 1 rodada |
| M4 | `quality.md`, `guard.md` e `security.md` gerados no SHA congelado | Bench com n ≥ 60 na classe principal; 12 conversas |

**Nunca cortar:**
- isolamento: RLS, grants, JWT, ferramentas sem identidade, `rkey`, segredos;
- EvidenceGuard com perturbação;
- escrita restrita;
- LLM comprometido;
- ataques (≥ 1 rodada);
- teste de qualidade (1 rodada);
- latência real;
- `docker compose up` + `make login`;
- replay;
- A2A mínimo com posse;
- **Phoenix mínimo** (AGENT → LLM → TOOL → GUARDRAIL);
- conversas;
- resumo executivo e documento.

**Agenda humana:**
- **Ter (noite):** chave do Gemini.
- **Cada marco:** 5 linhas próprias por decisão ★ da fase:
  - M1: D3, D23;
  - M2: D1, D2, D6, D21;
  - M3: D8, D15, D16, D17;
  - M4: D11.
- **Qua:** amostra dos nomes PT das disciplinas.
- **Sáb:** 30 rótulos às cegas (~1 h) + 24 respostas de ataque (~30 min) + 20 itens conferidos à mão (~20 min).
- **Dom:** ler o rascunho.
- **Seg:** reescrita, vídeo, ensaio e decisão da §12.

Portões assíncronos: segue-se se não houver objeção em 1 h.

---

## Fase 0 — Base, contratos e spikes

### Task 1: Base, segredos, compose, CI e relógio
**Depende de:** —

**Files:**
- Create: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `.gitattributes`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md`, `scripts/init_secrets.py`, `ops/postgres/init.sh`, `core/clock.py`, `app/config.py`, `etl/config.py`, `tests/conftest.py`
- Test: `tests/unit/test_config.py`, `tests/unit/test_clock_rule.py`, `tests/unit/test_init_secrets.py`

**Interfaces:**
- `init_secrets(root, uids={"db":999,"app":10001,"etl":10001})`:
  - gera uma vez `agent_app_password` e `etl_owner_password` e grava a mesma cópia nos volumes que precisam;
  - gera `jwt_secret`, `dev_login_secret` e `redis_password` (48 bytes urlsafe) e `prompt_canary` (`token_hex(16)`);
  - modo 0400, `chown` por volume, idempotente.
- `AppSettings`:
  - `settings_customise_sources`: segredos só do `secrets_dir=/run/secrets/app`;
  - campos da spec, mais `llm_backend: Literal["aistudio","vertex"]`, `gcp_project`, `gcp_location`, `llm_fallback_model="gemini-3.5-flash-lite"` e `limits_profile`;
  - recusa segredo vazio, com menos de 24 caracteres ou com `change-me`/`example`;
  - `otel_capture_content=True` exige `app_env="dev"`.
- `EtlSettings` (`/run/secrets/etl`).
- `core/clock.py`.
- Compose:
  - `init-secrets` como root, one-shot;
  - `db` (postgres:17) com `init.sh`;
  - `redis` com `--requirepass`;
  - `etl` (uid 10001) com `data/` ro, `secrets-etl`, `etl-out` e `eval-out`;
  - `api` (uid 10001) com `secrets-app` e `etl-out` ro, **sem** `data/`, em `127.0.0.1:8000`, com `PROMETHEUS_MULTIPROC_DIR`;
  - profiles `obs` (phoenix e prometheus em 127.0.0.1), `eval` e `langflow` (opcional).
- `Makefile`: `up`, `down`, `login`, `a2a-demo`, `test`, `test-full`, `test-live`, `lint`, `check`, `eval-*`, `load-test` e `package`.
- CI:
  - job `check` com step `psql` que cria roles e schemas;
  - job `compose-smoke`, ligado na Task 25.

- [ ] **Step 1: Write the failing tests**
  - `test_defaults`
  - `test_placeholder_secret_rejected`
  - `test_short_secret_rejected`
  - `test_canary_32_hex`
  - `test_env_cannot_override_secret`
  - `test_capture_content_requires_dev`
  - `test_app_settings_has_no_etl_secrets`
  - `test_init_secrets_shared_values_equal_across_volumes`
  - `test_init_secrets_owner_and_mode`
  - `test_init_secrets_idempotent`
  - `test_clock_only_in_core_clock`: casa `now()` e **não** casa `wall_now()`.
- [ ] **Step 2–4:** FAIL → implementar → PASS; `docker compose up init-secrets db redis` sobe e `init.sh` lê os segredos.
- [ ] **Step 5: Commit** — `chore: base com segredos gerados por consumidor, compose, CI e relógio único`.

### Task 2: Contratos centrais e fachadas
**Depende de:** 1

**Files:**
- Create: `core/rkey.py`, `core/scope.py`, `app/tools/base.py`, `app/tools/registry.py` (os 11 nomes com handlers stub), `app/agent/llm.py`, `app/data/card.py` (só o tipo), `app/obs/tracing.py` (fachada no-op), `app/obs/metrics.py` (declarações), `app/main.py` (todos os routers como stubs: dev, chat, protocols, feedback, a2a, health)
- Test: `tests/unit/test_contracts.py`, `tests/unit/test_rkey_rule.py`

**Interfaces:** as de "Interfaces centrais". `build_tools(channel)` devolve as 11 ferramentas; a disponibilidade vem de `Tool.allowed`.

- [ ] **Step 1–4:**
  - `test_rkey_format_includes_sub`
  - `test_no_redis_key_built_outside_rkey`: varre `app/` procurando `redis` + f-string/`+`/`%`.
  - `test_registry_has_11_tools_identical_specs_for_any_scope`
  - `test_draftcard_public_has_no_token`
  - `test_metrics_declared` (nomes e buckets da spec §9.4)

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: contratos centrais, chaves Redis com sub e fachadas de observabilidade`.

### Task 3: Spikes ao vivo
**Depende de:** — (scripts independentes com o SDK; roda às 9h, em paralelo)

**Files:**
- Create: `tests/live/test_spikes.py` (`live`), `docs/evidence/spikes.md`

- [ ] **Step 1: Gemini**
  1. `models.list` contém `gemini-3.5-flash-lite`, `gemini-3.8-flash` e `gemini-4-argon`. Se faltar algum, registrar os IDs disponíveis.
  2. Schema estrito com `enum` e `additionalProperties:false` é aceito; 2 voltas de function calling com `thought_signature`, sem erro 400.
  3. **Portão:** 40 chamadas por modelo com MINIMAL, prompt de ~1.500 tokens e 11 declarações. Registrar a distribuição.
  4. `cached_content_token_count` na 2ª requisição, feita para outro aluno com o mesmo prefixo e as mesmas declarações.
  5. `max_output_tokens` × raciocínio com MINIMAL/LOW: o limite inclui o raciocínio?
  6. Troca de modelo no meio da conversa com a `thought_signature` do outro modelo: aceita ou 400?
- [ ] **Step 2: A2A 1.0**
  - `DefaultRequestHandler(executor eco, InMemoryTaskStore(), card)` + `create_jsonrpc_routes(handler, "/a2a", builder_de_teste, enable_v0_3_compat=False)`.
  - Usuário A faz `SendMessage`; B faz `GetTask` → -32001; A faz `GetTask` → ok (controle positivo).
  - `CreateTaskPushNotificationConfig` → erro.
  - `SendMessage` de B com o `contextId` de A: registrar o comportamento do SDK.
  - Cliente A2A real do SDK contra o servidor.
- [ ] **Step 3: LangGraph** — grafo de 2 nós com `get_stream_writer` e cancelamento; `draw_mermaid` funciona.
- [ ] **Step 4:** Aplicar a fórmula pré-registrada (spec §6.5) e registrar em `spikes.md` as metas resultantes por classe e modelo.
- [ ] **Step 5: Commit** — `test(live): spikes de Gemini, A2A 1.0 e LangGraph com portão de latência`.

### Task 4: Provider Gemini, admissão e fakes
**Depende de:** 2, 3

**Files:**
- Create: `app/agent/gemini.py`, `app/agent/fakes.py`, `app/obs/cost.py`; Modify: `app/infra/redis_stores.py` (`AdmissionBucket` em Lua, RPM+TPM)
- Test: `tests/unit/test_gemini_adapter.py`, `tests/unit/test_admission.py`, `tests/unit/test_cost.py`

**Interfaces:**
- `GeminiProvider(settings)`:
  - `generate(model=…)`, com o backend AI Studio ou Vertex vindo de `llm_backend`;
  - retry do SDK desligado;
  - `parameters_json_schema`;
  - AFC desligado;
  - `ThinkingConfig`;
  - `max_output_tokens` conforme o spike;
  - `thought_signature` ↔ `provider_meta`, descartada quando o modelo do histórico difere;
  - `finish_reason`;
  - semáforo e fila (`LLMUnavailable("queue")`);
  - retry com piso de tempo, `RetryInfo`, teto de 10% e piso de 3/min.
- `AdmissionBucket.reserve(sub, est_tokens, deadline) -> bool`: script Lua; RPM e TPM globais.
- `ScriptedLLM`.
- `cost_usd(usage, model)`, com tabela datada que cobre entrada, saída com raciocínio e cache.
- `make_llm(settings)`.
- O `LoadTestLLM` fica para a Task 33.

- [ ] **Step 1–4:**
  - `test_parameters_json_schema`
  - `test_thought_signature_roundtrip`
  - `test_signature_dropped_on_model_switch`
  - `test_usage_and_model`
  - `test_retry_requires_time_left`
  - `test_retry_honors_retry_info`
  - `test_retry_cap_and_floor`
  - `test_queue_full`
  - `test_admission_rpm_tpm_lua` (fakeredis com Lua)
  - `test_vertex_backend_selected`
  - `test_cost_per_model`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): provider Gemini com deadline, retry controlado e admissão global RPM+TPM`.

---

## Fase 1 — ETL

### Task 5: Normalização, formatação e chaves [revisão por fase]
**Depende de:** 1

**Files:** Create `core/normalize.py`, `core/format.py`, `core/keys.py`. Test `tests/unit/test_normalize.py`, `test_format.py`, `test_keys.py`.

**Interfaces:**
- `ra_check_digit`, `normalize_ra`, `normalize_academic_ref`, `normalize_cpf`, `lms_login`
- `parse_epoch`, `parse_iso`, `parse_naive_local`, `parse_date`, `parse_brl`, `parse_bool`, `digits`
- `fmt_brl`, `fmt_date`, `fmt_instant`, `fmt_grade`, `fmt_pct`, `fmt_period` ("2026/2")
- `hmac_uuid`, `brand_family`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("base,dv", [("18001", "7"), ("18015", "7"), ("01253", "0"), ("18172", "2")])
def test_ra_check_digit(base, dv): assert ra_check_digit(base) == dv

@pytest.mark.parametrize("raw,ra", [("18001-7", "180017"), ("1253-0", "012530"), ("18015", "180157"),
                                    (" 01281-5 ", "012815"), ("012816", None), ("", None)])
def test_normalize_academic_ref(raw, ra): assert normalize_academic_ref(raw) == ra

def test_dates():
    assert fmt_date(date(2026, 11, 5)) == "05/11/2026"
    assert fmt_instant(datetime(2026, 11, 1, 2, 1, tzinfo=UTC)) == "31/10/2026 23:01"

def test_money_cpf_family():
    assert parse_brl("R$ 1.234,56") == 123456 and fmt_brl(4950843) == "R$ 49.508,43"
    assert normalize_cpf("8140522660") == "08140522660" and brand_family("UniSaoPaulo_EAD") == "UniSaoPaulo"

def test_hmac_uuid(): assert hmac_uuid(b"a" * 32, "x") == hmac_uuid(b"a" * 32, "x") != hmac_uuid(b"b" * 32, "x")
```

- [ ] **Step 2–5:** FAIL → implementar → PASS → commit `feat(core): normalização, formatação e chaves HMAC`.

### Task 6: Leitura, fixture e oráculo independente
**Depende de:** 5

**Files:** Create `etl/raw.py`, `scripts/make_fixture.py`, `tests/fixtures/mini/**`, `golden.json`, `evals/oracle.py`. Test `tests/unit/test_raw.py`, `tests/unit/test_oracle.py`, `tests/integration/test_oracle_full.py` (`fulldata`).

**Interfaces:**
- `read_raw(source: Path)`: aceita `data/dados.zip` ou um diretório.
- `make_fixture.py` (com `python3 -I`):
  - os 10 golden;
  - os 11 parceiros de colisão dos RAs golden;
  - o irmão 180548;
  - **1 protocolo de terceiro e 1 matrícula LOCKED escolhidos à mão**;
  - toda linha ligada a eles;
  - linhas a descartar presentes;
  - catálogos inteiros;
  - menos de 3 MB.
- `evals/oracle.py`: só `csv`, sem importar `etl/` nem `app/`.
  - `oracle_link_protocols`: regras completas; os 17 casos ambíguos são resolvidos pelos sinônimos de `Course_Label`.
  - `oracle_lms_accounts`: SSO + manuais, a partir de `user_loggedin`.
  - Funções por domínio: `oracle_financial`, `oracle_snapshot_check`, `oracle_courses`, `oracle_forecast`, `oracle_protocols`, `oracle_lms` (só inscrição ativa; `simulados_abertos`, `proxima_avaliacao`, encerradas sem tentativa), `oracle_enrollment_events`, `oracle_term_registration`, `oracle_catalog`.
  - `oracle_coverage(raw)`.
  - `ideal_answer(item) -> str`, para o autoteste.

- [ ] **Step 1–4:**
  - `test_read_raw_47_from_zip_and_dir`
  - `test_fixture_composition`: 11 parceiros, 1 terceiro, 1 LOCKED, < 3 MB.
  - `test_oracle_golden4`: R$ 49.508,43 / R$ 42.636,26 / 32.
  - `test_oracle_golden1_attempts == 7`
  - `fulldata`:
    - protocolos 3911 (3455/73/29/337/17), 22 sem vínculo;
    - LMS 1003;
    - snapshot 273/273;
    - provas que valem nota encerradas sem tentativa == 21;
    - simulados abertos == 1221;
    - `PROTOCOL_REF` 229.

  FAIL → implementar e gerar a fixture → PASS.
- [ ] **Step 5: Commit** — `feat(evals): leitura, fixture e oráculo independente com cobertura completa`.

### Task 7: Ontologia e nomes PT
**Depende de:** 6

**Files:** Create `ontology/entities.yaml`, `enums.yaml`, `tables.yaml`, `free_text_templates.yaml`, `subject_names_pt.yaml`, `ontology/tools/`, `core/ontology.py`, `scripts/gen_subject_names.py`. Test `tests/unit/test_ontology.py`, `tests/integration/test_ontology_full.py` (`fulldata`).

**Interfaces:**
- `Ontology` com `.sensitivity`, `.is_free_text`, `.label_pt`, `.table_destination`/`.reason` (`customer` = `resolution_only`), `.tool_outputs`, `.service_name_pt`, `.course_name_pt` e `.subject_name_pt`.
- `.classify_message(text) -> (modelo_pt | None)`: casa com os 4 modelos da allowlist.
- `.quiz_type(name) -> Literal["AV1","AV2","simulado"] | None`.
- `subject_names_pt.yaml`: 260 nomes gerados uma vez pelo script com Gemini; amostra de 20 revisada.

- [ ] **Step 1–4:**
  - `test_47_tables_destination_reason`
  - `test_customer_resolution_only`
  - `test_internal_and_free_text_marked`
  - `test_services_courses_subjects_pt` (sem skip)
  - `test_classify_message_templates`
  - `test_quiz_type`
  - `fulldata`:
    - `test_enum_labels_cover_full_dataset`
    - `test_message_typing_covers_99pct` (dos 14.853, os não classificados ≤ 1%)

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: ontologia com nomes PT, tipificação do texto livre e destino das 47 tabelas`.

### Task 8: Resolução core
**Depende de:** 5

**Files:** Create `etl/resolve_core.py`. Test `tests/unit/test_resolve_core.py`.

**Interfaces:** `Person`, `Enrollment`, `resolve_people`, `resolve_enrollments`, `brand_for_company`, `resolve_contracts`, `ResolutionStats`.

- [ ] **Step 1–4:**
  - `test_same_cpf_one_person`
  - `test_colliding_ra_distinct`
  - `test_academic_ref_variants`
  - `test_never_by_payer`
  - `test_unresolved_reported`
  - `test_first_name_title_case`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução de pessoas, matrículas e contratos`.

### Task 9: Resolução LMS
**Depende de:** 8

**Files:** Create `etl/resolve_lms.py`. Test `tests/unit/test_resolve_lms.py`.

**Interfaces:** `resolve_lms_accounts`, `is_student_login` (`^(PRES|EAD)_\d{5}$`), `tenant_brand`.

- [ ] **Step 1–4:**
  - `test_sso_trim_upper`
  - `test_other_tenant`
  - `test_manual_by_login_event`
  - `test_non_students_dropped` (`prof40013`, `aluno.teste3`, `admin`)
  - `test_ambiguous_manual_not_linked`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução das contas LMS`.

### Task 10: Resolução secretaria
**Depende de:** 8

**Files:** Create `etl/resolve_secretary.py`. Test `tests/unit/test_resolve_secretary.py`.

**Interfaces:** `resolve_protocols(raw, people, enrollments, contracts)` com as regras `ra_cpf`, `guardian_payer`, `guardian_payer_unique`, `cpf_brand_unique` e `cpf_brand_by_date`; `protocol_number`.

- [ ] **Step 1–4:**
  - `test_stu_id_without_zeros`
  - `test_typo_by_cpf`
  - `test_cpf_mismatch_not_by_ra`
  - `test_guardian_payer_with_stu_id`
  - `test_guardian_payer_unique_without_stu_id_linked`
  - `test_guardian_paying_two_not_linked`
  - `test_third_party_not_linked`
  - `test_by_date`
  - `test_invariant_student_or_payer`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução dos protocolos com responsável pagador único`.

### Task 11: Schema, RLS e grants
**Depende de:** 7

**Files:** Create `etl/schema.sql`, `etl/rls.sql`, `etl/grants.yaml`, `tests/integration/conftest.py`. Test `tests/integration/test_rls.py`.

**Interfaces:**
- `schema.sql` cria `canon` e `agent_rw` (spec §5.2):
  - `agent_rw.protocol_request` com `draft_id UNIQUE` e `CHECK` do serviço, sem índice por serviço;
  - grants da sequence.
- `rls.sql` faz ENABLE/FORCE, cria as políticas e aplica os grants de `grants.yaml`.
- Fixture `db_dsns`.

- [ ] **Step 1: Write the failing tests**
  - `test_copy_then_rls_order_works` (dono NOSUPERUSER)
  - `test_copy_after_force_rls_fails` (documenta o motivo da ordem)
  - `test_no_setting_nothing`
  - `test_other_person_nothing`
  - `test_catalog_family_scope`
  - `test_insert_other_person_rejected`
  - `test_check_rejects_service`
  - `test_roles_flags`
  - `test_grants_match_allowlist`: sem TRUNCATE/DELETE, sem membership.
  - `test_force_rls_on_all_non_global_tables`
  - `test_canon_rebuild_preserves_agent_rw`
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): schemas, RLS aplicado depois da carga e grants por allowlist`.

### Task 12: Transformações e tipificação
**Depende de:** 7, 9, 10

**Files:** Create `etl/build.py`. Test `tests/unit/test_build.py`.

**Interfaces:**
- `build_academic`: inclui `divergencia` em `class_result`.
- `build_finance`.
- `build_protocols`:
  - `solicitante_papel`;
  - `protocol_message` com `autor_papel`, `modelo` ou `texto_livre`;
  - mensagens do próprio aluno omitidas.
- `build_lms`:
  - `lms_quiz` só de inscrição ativa, com tipo e disciplina PT do SAG (sala → `SAG-<CLASS_ID>` → disciplina);
  - `lms_event` sem IP/URL.
- `build_catalog`: preço com `mensalidade_ano` e `parcelas_por_semestre`.
- `build_coverage`: derivada dos dados.

- [ ] **Step 1–4:**
  - `test_internal_messages_dropped`
  - `test_no_analyst_login_anywhere`: os 25 logins ausentes de todas as linhas.
  - `test_message_typed_or_untrusted`
  - `test_student_own_messages_omitted`
  - `test_lms_quiz_active_only_and_typed`
  - `test_room_name_from_sag_subject_pt`
  - `test_divergence_flag`
  - `test_coverage_sag_events_from_2020_12_12`
  - `test_due_date_is_date`
  - `test_payer_type_golden4`
  - `test_issued_documents_golden9_diploma`
  - `test_enrollment_event_protocol_ref_golden3`: valor exato lido do CSV.

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): transformações com texto livre tipificado e cobertura derivada`.

### Task 13: Carga, relatório e arquivos de login
**Depende de:** 11, 12

**Files:** Create `etl/load.py`, `etl/report.py`, `etl/__main__.py`. Test `tests/integration/test_load.py`, `tests/integration/test_full_counts.py` (`fulldata`).

**Interfaces:**
- `load_all(raw, settings)`: uma transação que recria `canon`, faz COPY e aplica o `rls.sql`.
- `write_report`: igualdade por regra, invariantes, snapshot, cobertura do oráculo e logins ausentes.
- `write_golden_login` (`etl-out`).
- `write_eval_students` (`eval-out`; sem CPF; com estratos).
- Fixture `loaded_db`.

- [ ] **Step 1–4:**
  - `test_load_matches_oracle`
  - `test_schema_matches_ontology`
  - `test_reload_keeps_agent_rw`
  - `test_login_files_no_cpf`
  - `fulldata`:
    - 1000 / 1037 / 1065;
    - protocolos 3911 com a quebra exata;
    - LMS 1003;
    - eventos 229;
    - snapshot 273/273.

  FAIL → implementar → PASS; `docker compose up etl` gera o relatório.
- [ ] **Step 5: Commit** — `feat(etl): carga com RLS no fim, relatório de qualidade e arquivos de login`.
- [ ] **Step 6:** Revisão da fase. **M1.**

---

## Fase 2 — Dados e ferramentas

### Task 14: Escopo, conexão e registro
**Depende de:** 2, 13

**Files:** Create `app/data/db.py`; Modify `app/infra/redis_stores.py` (`DraftStore`, `LabelCache`, `LoginTickets`). Test `tests/unit/test_tool_base.py`, `tests/integration/test_scope.py`.

**Interfaces:**
- **Conexão:** `create_pool` com reset; `assert_safe_role`; `scoped(pool, person_key_from_jwt, families)`.
- **Escopo:** `load_scope`, com `LabelCache` só para rótulos.
- **Matrícula:** `enrollment_param`; `resolve_enrollment(scope, index | None, prefer_status: str | None)`.
- **Ferramentas:** `validate_args`; `Tool.allowed` padrão.
- **Rascunhos:** `DraftStore.put/take/pending(sub, conversation_id)`, com chaves via `rkey`.

- [ ] **Step 1–4:**
  - `test_specs_identical_across_students`
  - `test_index_resolution_and_nonexistent`
  - `test_prefer_status_graduated_golden9`
  - `test_extra_arg_rejected`
  - `test_draft_single_use_owner_bound`
  - `test_pool_reset`
  - `test_assert_safe_role`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: escopo, conexão com reset e matrícula padrão por status`.

### Task 15: Regras puras
**Depende de:** 2, 5

**Files:** Create `app/rules/finance.py`, `academic.py`, `lms.py`, `protocols.py`, `eligibility.py`, `coverage.py`. Test `tests/unit/test_rules.py`.

**Interfaces:**
- **Finanças:** `financial_position(...) -> Position(overdue_principal, open_charges, total_due, upcoming, next_due_date, next_due_amount, days_to_next_due, overdue_count)`.
- **Acadêmico:** `graduation_forecast`.
- **LMS:** `quiz_status`; `lms_summary(quizzes, attempts, today) -> (simulados_abertos, proxima_avaliacao, pendentes)`.
- **Protocolos:** `sla_due`; `protocol_status_pt`.
- **Elegibilidade:** `eligibility(service, facts) -> Eligible | NotEligible(motivo, alternativa)`, que checa protocolo aberto primeiro.
- **Cobertura:** `coverage_note`.

- [ ] **Step 1–4:**
  - `test_reversed`
  - `test_partial_two_parts`
  - `test_renegotiated_excluded`
  - `test_charges_already_paid` (7067951: 259 / 348)
  - `test_position_reference_only`
  - `test_quiz_status`
  - `test_lms_summary_separates_practice`
  - `test_forecast`
  - Elegibilidade:
    - `test_eligibility_diploma_graduated`
    - `test_eligibility_diploma_already_issued_offers_doc041`
    - `test_eligibility_second_call`
    - `test_eligibility_slip`
    - `test_eligibility_reactivation_locked`
    - `test_eligibility_renegotiation_overdue`
    - `test_eligibility_doc001_active`
    - `test_eligibility_open_protocol_same_service`
  - `test_coverage_note`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(rules): regras puras com elegibilidade completa`.

### Task 16: Ferramentas acadêmicas
**Depende de:** 14, 15

**Files:** Create `app/data/queries/academic.py`, `app/tools/academic.py`, `ontology/tools/academic.yaml`; Modify `registry.py` (só os stubs deste domínio). Test `tests/integration/test_tools_academic.py`.

**Interfaces:** `get_academic_profile`, `list_course_results` (disciplina fora da matriz → "não faz parte da sua matriz") e `get_curriculum_progress`. O campo `outras_matriculas` só aparece se houver mais de uma.

- [ ] **Step 1–4:** testes contra o oráculo:
  - `test_profile_golden1`
  - `test_golden9_documents`
  - `test_status_history_golden3`
  - `test_cancelled_over_registered`
  - `test_subject_pt_calculo`
  - `test_subject_not_in_curriculum`
  - `test_attendance`
  - `test_divergence_flag_surfaced`
  - `test_progress_forecast`
  - `test_full_list_for_term`
  - `test_sag_coverage_note`
  - `test_single_enrollment_no_other_field`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): ferramentas acadêmicas`.

### Task 17: Ferramentas financeiras
**Depende de:** 14, 15

**Files:** Create `app/data/queries/finance.py`, `app/tools/finance.py`, `ontology/tools/finance.yaml`; Modify `registry.py`. Test `tests/integration/test_tools_finance.py`.

**Interfaces:** `get_financial_summary`, `list_invoices` (`period` por competência, com o vencimento em cada item) e `list_agreements`. Chaves em PT.

- [ ] **Step 1–4:**
  - `test_golden4_total_para_quitar`
  - `test_no_sibling_invoices`
  - `test_golden9_breakdown`
  - `test_golden1_no_agreements`
  - `test_golden6_active_agreement`
  - `test_cap_20`
  - `test_reference_date`
  - `test_period_is_competence`
  - `test_finance_coverage_note`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): ferramentas financeiras`.

### Task 18: Protocolos e rascunho
**Depende de:** 14, 15

**Files:** Create `app/data/queries/protocols.py`, `app/tools/protocols.py`, `ontology/tools/protocols.yaml`; Modify `registry.py`. Test `tests/integration/test_tools_protocols.py`.

**Interfaces:**
- `list_protocols`: sem descrições.
- `get_protocol`: `untrusted` só se houver `texto_livre`.
- `draft_protocol`:
  - `allowed`: recusa com `channel="a2a"` ou `tainted`;
  - elegibilidade;
  - `prefer_status` para DOC-040/041/ACA-011;
  - `DraftCard` com token;
  - `data` sem token;
  - registra o rascunho pendente em `(sub, conversation_id)`.
- `orientation(service)`: prazo, taxa, canais e por que o agente não executa.

- [ ] **Step 1–4:**
  - `test_foreign_and_nonexistent_same`
  - `test_internal_notes_absent`
  - `test_guardian_protocol_without_identity`
  - `test_sibling_absent`
  - `test_invoice_link`
  - `test_sla_due_golden3`
  - `test_pending_requester_label`
  - `test_typed_messages_trusted`
  - `test_planted_text_untrusted`
  - `test_draft_refused_when_tainted_or_a2a`
  - `test_draft_not_eligible_active_diploma`
  - `test_draft_golden9_doc041_alternative`
  - `test_draft_open_protocol_blocks`
  - `test_draft_token_not_in_data`
  - `test_sa_readable`
  - `test_orientation_lock`
  - `test_secretary_coverage_note`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): protocolos e rascunho com elegibilidade e recusa na execução`.

### Task 19: LMS e catálogo
**Depende de:** 14, 15

**Files:** Create `app/data/queries/lms.py`, `catalog.py`, `app/tools/lms.py`, `catalog.py`, `ontology/tools/lms.yaml`, `catalog.yaml`; Modify `registry.py`. Test `tests/integration/test_tools_lms_catalog.py`.

**Interfaces:** `get_lms_activity` (confiável, porque o texto é tipificado) e `get_catalog` (família da marca; preço com unidade).

- [ ] **Step 1–4:**
  - `test_golden1_7_attempts`
  - `test_golden2_30_attempts`
  - `test_pending_vs_oracle`
  - `test_practice_separate`
  - `test_no_lms_account`
  - `test_lms_before_coverage`
  - `test_accesses_in_period`
  - `test_event_no_ip_url`
  - `test_lms_output_trusted`
  - `test_catalog_family`
  - `test_price_direito_with_unit`
  - `test_calendar_av_dates`
  - `test_services_orientation`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): LMS tipificado e catálogo por família da marca`.

### Task 20: Cartão, exposição e LLM comprometido
**Depende de:** 16, 17, 18, 19

**Files:** Implement `app/data/card.py`; Create `tests/adversarial.py`, `tests/integration/test_card.py`, `test_tool_exposure.py`, `test_compromised_llm.py`.

**Interfaces:**
- `build_student_card(conn, scope, settings) -> StudentCard`, com o conteúdo da spec §6.2 em ≤ 400 tokens.
- `AdversarialLLM`.

- [ ] **Step 1–4:**
  - `test_card_golden4_vs_oracle` (inclui tipo de pagador, valor e data do próximo boleto)
  - `test_card_golden9_diploma_line`
  - `test_card_next_assessment_and_practice`
  - `test_card_budget`
  - `test_tool_outputs_within_declared_fields`
  - `test_compromised_llm_never_sees_other_person`: golden 1, 2, 3, 4 e 9; ~600 chamadas.
  - `test_fuzzed_args`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: cartão do aluno; test(security): exposição e LLM comprometido`.
- [ ] **Step 6:** Revisão da fase.

---

## Fase 3 — Agente (raia B, paralela)

### Task 21: Guards e conjunto de perturbação
**Depende de:** 2, 6 (o oráculo fornece os dados das respostas corretas)

**Files:** Create `app/guards/input.py`, `app/guards/evidence.py`, `evals/guard/perturb.py`. Test `tests/unit/test_input_guard.py`, `tests/unit/test_evidence_guard.py`, `tests/unit/test_perturbation.py`.

**Interfaces:**
- **Entrada:** `normalize_input`.
- **Extração:** `extract_claims`, com `kind` ∈ money, date, period, pct, decimal, int, id, cpf, cnpj, email, phone, url, status, polarity, action.
- **Checagem:** `check_evidence(answer, *, evidence, card_facts, enrollment_indices, user_text, system_notes, canary, prompt_prefix, vocabulary)`. Regras:
  - polaridade por grade (`NEGATORS` × `NOUNS` → predicado), exigindo todas as matrículas ou a matrícula citada;
  - ação (léxico ampliado) → `SA-` só de `system_notes`;
  - URL → bloqueada;
  - status em frase que cita entidade.
- **Saída:** `degrade(...)`; `template_fallback(last_tool, data, channel, tainted) -> str`; `strip_markdown`.
- **Perturbação:** `evals/guard/perturb.py` é escrito numa **sessão de subagente que não vê o `evidence.py`**. Ele parte das respostas ideais do oráculo e aplica mutações de 1 fato por tipo, mais paráfrases de ação, negação e URL.

- [ ] **Step 1: Write the failing tests**

```python
TOOL = ['{"total_para_quitar": "R$ 49.508,43", "proximo_vencimento": "05/11/2026", "faturas_vencidas": 32, '
        '"frequencia": "75%", "status": "ativa", "protocolo": "20260024855"}']
FACTS = {"1.total_para_quitar": "R$ 49.508,43", "2.total_para_quitar": "R$ 0,00", "1.provas_pendentes": "0"}
def chk(a, user="", notes=()):
    return check_evidence(a, evidence=TOOL, card_facts=FACTS, enrollment_indices=("1", "2"), user_text=user,
                          system_notes=notes, canary="c" * 32, prompt_prefix=PREFIX, vocabulary=VOCAB)

def test_grounded(): assert chk("Você deve R$ 49.508,43 em 32 faturas; vence em 05/11.").ok
def test_written_date(): assert chk("Vence dia 5 de novembro.").ok and not chk("Vence dia 6 de novembro.").ok
def test_period_not_int(): assert chk("Seus acessos em 2026/2 aparecem abaixo.", user="acessos em 2026/2").ok
def test_mid_int_and_pct(): assert not chk("Você tem 31 faturas.").ok and not chk("Frequência de 80%.").ok
def test_status_entity_sentence(): assert not chk("Situação da matrícula: trancada.").ok
def test_polarity_per_enrollment(): assert not chk("Você está em dia com as mensalidades.").ok
def test_polarity_cited_enrollment(): assert chk("Na matrícula 2 você está em dia.").ok
def test_action_paraphrase(): assert not chk("Pronto, seu pedido foi enviado.").ok
def test_action_user_sa_not_proof(): assert not chk("Abri a solicitação SA-2026-000123.", user="confirme a SA-2026-000123").ok
def test_action_system_note_ok(): assert chk("Abri a solicitação SA-2026-000123.", notes=("Solicitação SA-2026-000123 aberta em 31/10/2026",)).ok
def test_url_blocked(): assert not chk("Veja em http://exemplo.test/pagar").ok
def test_no_substring(): assert not chk("Você deve R$ 42,00.").ok
def test_template_fallback_uses_fields():
    assert "R$ 49.508,43" in template_fallback("get_financial_summary", {"total_para_quitar": "R$ 49.508,43"}, "web", False)
```

`test_perturbation_recall_ge_95_money_date_id`, `test_perturbation_fpr_le_5`.

- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(guards): EvidenceGuard com polaridade por matrícula, ação, URL e perturbação independente`.

### Task 22: Prompt e grafo
**Depende de:** 4, 14, 21 (usa `StudentCard` do contrato da Task 2; o cartão real chega na Task 20)

**Files:** Create `app/agent/prompt.py`, `app/agent/messages.py`, `app/agent/graph.py`. Test `tests/unit/test_prompt.py`, `tests/unit/test_graph.py`.

**Interfaces:**
- **Prompt:**
  - `SYSTEM_PREFIX` (spec §6.2);
  - `card_message`;
  - `wrap_tool_result`.
- **Mensagens fixas:**
  - `DRAFT_SHOWN_TEMPLATE`;
  - `DRAFT_PENDING_USE_BUTTON`;
  - `TAINT_NO_DRAFT`;
  - `FERRAMENTA_INDISPONIVEL`;
  - `DEGRADE_NOTICES`.
- **Grafo:**
  - `build_graph(llm, settings)`;
  - `run_turn(..., model, deadline, writer)`.
- **`run_tools`:**
  - aplica `Tool.allowed(ctx)` antes de executar;
  - `draft_protocol` emitido → a resposta do turno é `DRAFT_SHOWN_TEMPLATE`, sem segunda chamada ao LLM.
- **Taint e evidência:**
  - taint = `untrusted` na janela ou no turno;
  - a evidência exclui `texto_livre`.
- **Limites:**
  - teto global ≥ 80% → `max_rounds = 2` e sem retry do guard;
  - `recursion_limit = 2·rounds + 6`.
- **`draw_mermaid()`** exportado para `docs/evidence/graph.md`.

- [ ] **Step 1–4:**
  - `test_card_answers_in_one_call`
  - `test_tool_then_answer`
  - `test_parallel_tools`
  - `test_invalid_args_not_executed`
  - `test_undeclared_or_refused_tool_call_rejected`
  - `test_tainted_draft_call_rejected_and_message`
  - `test_taint_persists_next_turn`
  - `test_taint_clears_after_window`
  - `test_lms_then_second_call_request_not_tainted` (texto tipificado)
  - `test_free_text_not_evidence`
  - `test_draft_turn_text_fixed`
  - `test_guard_retry_pass`
  - `test_guard_degraded_typed`
  - `test_guard_template_fallback`
  - `test_worst_path_within_recursion_limit`
  - `test_budget_exceeded`
  - `test_llm_unavailable`
  - `test_degraded_mode_two_rounds_no_retry`
  - `test_thought_signature_preserved`
  - `test_history_cut_by_turn_and_compacted`
  - `test_progress_and_first_event_ms`
  - `test_system_and_tools_identical_across_students_and_channels`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): grafo LangGraph com cartão, recusa na execução e EvidenceGuard`.

### Task 23: Replay [revisão por fase]
**Depende de:** 22

**Files:** Create `app/agent/replay.py`, `evals/replays/` (gravações em `evals/replays/<label>.jsonl`). Test `tests/unit/test_replay.py`.

**Interfaces:** `RecordingLLM`, `ReplayLLM`, `ReplayLLM.suggested`. `make_llm` passa a aceitar `replay`.

- [ ] **Step 1–5:** `test_record_then_replay`, `test_unknown_question_message` → FAIL → implementar → PASS → commit `feat(agent): modo replay`. Merge das raias. Revisão da fase.

---

## Fase 4 — API, A2A, UI e observabilidade

### Task 24: Autenticação e dev
**Depende de:** 14

**Files:** Create `app/api/auth.py`, `app/api/dev.py`, `scripts/login_url.py`; Modify `app/main.py` (substitui os stubs). Test `tests/e2e/test_auth.py`.

**Interfaces:**
- **Tokens:**
  - `issue_token(person_key, aud, settings)`, com `wall_now()`;
  - `verify_token(token, aud, settings)`.
- **Leitura da identidade:** `get_person_cookie` (`aud=chat`) e `get_person_a2a` (bearer ou `x-api-key`, `aud=a2a`).
- **`/dev/login`** (só com `dev_login`):
  - `GET ?ticket=` consome um `LoginTickets` de uso único;
  - `POST` com o segredo;
  - só golden.
- **`/dev/a2a-token`:** mesma trava.
- **`scripts/login_url.py CASE`:** roda no container, cria o ticket e imprime a URL. É o alvo de `make login CASE=4`.
- **`/healthz`.**
- **Lifespan:** checagens de role, segredos e Redis.

- [ ] **Step 1–4:**
  - `test_tampered_401`
  - `test_expired_401`
  - `test_alg_none_401`
  - `test_wrong_alg_401`
  - `test_wrong_aud_401`
  - `test_x_api_key_accepted_for_a2a_only`
  - `test_body_identity_ignored`
  - `test_dev_disabled_404`
  - `test_ticket_single_use_and_expiry`
  - `test_wrong_secret_403`
  - `test_non_golden_403`
  - `test_cookie_flags`
  - `test_healthz`
  - `test_startup_aborts_superuser_or_bad_secret`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): JWT por canal, x-api-key no A2A, login por ticket e healthz`.

### Task 25: TurnService, chat SSE e `compose-smoke`
**Depende de:** 20, 22, 24

**Files:** Create `app/service/turn.py`, `app/api/chat.py`; Modify `app/infra/redis_stores.py`, `.github/workflows/ci.yml` (job `compose-smoke`). Test `tests/unit/test_turn_service.py`, `tests/e2e/test_chat.py`.

**Interfaces:** `TurnService.handle`, nesta ordem:
1. `normalize_input`;
2. rate limit → orçamento → teto;
3. **admissão** (`AdmissionBucket.reserve`; sem cota → `blocked_limit`, com 503 e `Retry-After` no SSE);
4. "sim" contextual (último turno emitiu rascunho + confirmação curta) → fixo;
5. histórico, escopo e cartão;
6. `run_turn`;
7. histórico gravado com `untrusted`;
8. span AGENT e métricas pela fachada;
9. `TurnResult.first_event_ms`.

`POST /chat` emite os eventos SSE `meta` (`trace_id`), `progress`, `draft` (`card.public()` + token), `answer`, `error` e `ping`.

`compose-smoke`:
1. fixture como `data/`;
2. `LLM_PROVIDER=fake` (`ScriptedLLM` que chama `get_financial_summary` e copia `total_para_quitar`);
3. `make login CASE=4`;
4. resposta contém "R$ 49.508,43";
5. `make a2a-demo`;
6. `docker compose exec api` sem `/data` nem `/run/secrets/etl`.

- [ ] **Step 1–4:**
  - `test_progress_then_answer`
  - `test_identity_claim_ignored`
  - `test_rate_limit_429`
  - `test_budget_exhausted`
  - `test_admission_503_retry_after`
  - `test_global_cap_degrades_then_503`
  - `test_history_isolated_by_rkey`
  - `test_llm_unavailable_error_no_traceback`
  - `test_extra_body_422`
  - `test_long_input_error`
  - `test_disconnect_cancels`
  - `test_markdown_stripped`
  - `test_yes_only_after_draft_turn`
  - `test_yes_unrelated_goes_to_llm`

  FAIL → implementar → PASS; `compose-smoke` verde.
- [ ] **Step 5: Commit** — `feat(service): TurnService com admissão global; chat SSE; ci: compose-smoke`. **M2.**

### Task 26: Confirmação, feedback, CSRF e IDOR
**Depende de:** 25

**Files:** Create `app/api/protocols.py`, `app/api/feedback.py`; Modify `app/main.py` (substitui os stubs). Test `tests/e2e/test_confirm_feedback.py`.

**Interfaces:**
- `POST /protocols/confirm`, nesta ordem:
  1. `Origin` + JSON;
  2. `take`;
  3. revalida serviço e elegibilidade;
  4. INSERT idempotente por `draft_id`;
  5. grava `system_note` com o `SA-` aberto.
- `POST /feedback`: grava a tabela + anotação no Phoenix pela fachada.

- [ ] **Step 1–4:**
  - `test_confirm_once_concurrent`
  - `test_other_students_draft_409`
  - `test_csrf_403`
  - `test_second_gen090_allowed`
  - `test_duplicate_same_service_not_eligible`
  - `test_description_edited_saved`
  - `test_confirm_note_grounds_action_claim`
  - `test_sa_dates_from_reference`
  - `test_feedback_isolated`
  - `test_idor_conversation_feedback`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): confirmação idempotente, feedback e proteções CSRF/IDOR`.

### Task 27: A2A 1.0
**Depende de:** 25

**Files:** Create `app/api/a2a.py`, `scripts/a2a_demo.py`, `ops/langflow/flow.json` (opcional); Modify `app/main.py` (substitui o stub), `app/infra/redis_stores.py` (`RedisTaskStore`). Test `tests/e2e/test_a2a.py`.

**Interfaces:**
- `build_agent_card(settings)`:
  - esquemas bearer e apiKey;
  - `AgentCapabilities(streaming=False, push_notifications=False)`.
- `JwtContextBuilder`: lê bearer ou `x-api-key` e preenche `ServerCallContext.user`.
- `RedisTaskStore`, com chaves `rkey("a2a_task", sub, "a2a", task_id)`.
- `StudentAgentExecutor`:
  - aceita 1 TextPart;
  - `conversation_id = rkey("conv", sub, "a2a", contextId)`;
  - delega a `TurnService.handle(channel="a2a")`.
- Montagem: `create_jsonrpc_routes(handler, "/a2a", builder, enable_v0_3_compat=False)` + card routes + `add_a2a_routes_to_fastapi`.
- `scripts/a2a_demo.py`: roda no container (`make a2a-demo`), emite o token, lê o card e envia `SendMessage`.

- [ ] **Step 1–4:**
  - `test_card_push_streaming_false`
  - `test_no_credentials_rejected`
  - `test_chat_token_rejected`
  - `test_x_api_key_works`
  - `test_answers_for_owner`
  - `test_identity_in_text_ignored`
  - `test_datapart_rejected`
  - `test_draft_refused_over_a2a`
  - `test_rate_limit_and_admission_over_a2a`
  - `test_ownership_by_method`, parametrizado:
    - `GetTask`, `CancelTask` e `ListTasks` alheios → -32001;
    - controle positivo: o dono consegue;
    - `SubscribeToTask` → não suportado.
  - `test_push_config_rejected`
  - `test_foreign_context_id_gets_empty_own_history`
  - `test_v03_method_names_not_served`

  FAIL → implementar → PASS.
- [ ] **Step 5:** (opcional, corta no M3) Profile `langflow` com `LANGFLOW_SSRF_ALLOWED_HOSTS=api`, o fluxo exportado e uma captura de tela em `docs/evidence/img/`.
- [ ] **Step 6: Commit** — `feat(api): A2A 1.0 de consulta com posse por método, x-api-key e mesmos limites`.

### Task 28: Chat web [revisão por fase]
**Depende de:** 25

**Files:** Create `app/web/*`; Modify `app/main.py` (substitui o stub). Test `tests/e2e/test_web.py`.

**Interfaces:**
- Progresso; cartão do rascunho; Confirmar; 👍/👎; botão "Nova conversa"; perguntas sugeridas no replay.
- Renderização só com `textContent`.
- Cabeçalhos CSP, `nosniff` e `no-referrer`.

- [ ] **Step 1–4:** `test_security_headers`, `test_js_no_innerhtml`, `test_new_conversation_button` → FAIL → implementar → PASS.
- [ ] **Step 5:** Teste manual com o golden 4: "quanto devo?" → "2ª via do histórico" → "sim" → Confirmar. Capturas em `docs/evidence/img/`.
- [ ] **Step 6: Commit** — `feat(web): chat em texto puro com cartão de confirmação`.

### Task 29: Observabilidade
**Depende de:** 25, 27 (instrumentação feita pela fachada desde a Task 2; esta tarefa liga exportadores, anotações e SLO)

**Files:** Modify `app/obs/tracing.py`, `app/obs/metrics.py` (multiprocesso); Create `app/obs/annotations.py`, `app/obs/logging.py`, `ops/prometheus/prometheus.yml`, `docs/SLO.md`. Test `tests/unit/test_obs.py`, `tests/e2e/test_observability.py`.

**Interfaces:**
- **Tracing:** `setup_tracing(settings)` com OTLP para o Phoenix.
  - Spans AGENT/LLM/TOOL/GUARDRAIL com:
    - `llm.model_name`;
    - `llm.token_count.prompt`, `.completion`, `.prompt_details.cache_read` e `.completion_details.reasoning`;
    - `gen_ai.*`;
    - argumentos enumerados das ferramentas e contagens;
    - `guard.outcome`, `llm.calls`, `prompt.sha` e `tools.sha`.
  - Conteúdo só com `otel_capture_content`.
  - Exceção registrada só pelo tipo.
- **`annotate(trace_id, name, label, score, explanation)`:** usa a API de anotações do Phoenix. Quem chama é o runner das evals e o feedback.
- **Logs:** `configure_logging`.
- **Métricas:** todas as da spec §9.4, em modo multiprocesso.
- **`SLO.md`:** latência por classe, TTFE, erro (`turns_total`), custo e **qualidade** (degradado + fallback ≤ 5%; 👎 ≤ 10%), mais o alerta proposto.

- [ ] **Step 1–4:**
  - `test_spans_no_content_by_default` (atributos e eventos)
  - `test_exception_type_only`
  - `test_capture_content_only_dev`
  - `test_span_kinds_and_token_details`
  - `test_tool_args_enumerated_no_pii`
  - `test_metrics_multiprocess_and_buckets`
  - `test_annotation_called_with_trace_id`
  - `test_logs_no_pii`

  FAIL → implementar → PASS.
- [ ] **Step 5:** `docker compose --profile obs up`, uma conversa e uma anotação → capturas em `docs/evidence/observability.md`.
- [ ] **Step 6: Commit** — `feat(obs): traces OpenInference sem conteúdo, anotações de qualidade, métricas e SLOs`. Revisão da fase. **M3.**

---

## Fase 5 — Evals

O código é escrito na quinta e na sexta, e as rodadas acontecem no sábado, uma depois da outra.

### Task 30: Dataset, plano de análise e acesso das evals
**Depende de:** 6, 13 (raia C, quinta)

**Files:** Create `evals/tokens.py`, `evals/client.py`, `evals/golden/templates.yaml`, `paraphrases.yaml`, `multiturn.yaml`, `build.py`, `analysis_plan.md`, `dataset.json`, `dataset.sha256`. Test `tests/unit/test_dataset.py`.

**Interfaces:**
- `tokens.mint`, executado dentro do container `evals`.
- `ChatClient`, com `ask`, `confirm` e `a2a_send`.
- `select_test_students(seed=20261103)`: 40 alunos com cotas.
- `templates.yaml`:
  - **≥ 50 modelos**, sendo **≥ 15 `test_only`**;
  - campos: `category`, `expected_tools`, `required`, `forbidden`, `expect_draft`.
  - As categorias seguem a spec §9.2, inclusive "consulta → pedido".
- `paraphrases.yaml`, também com `test_only`.
- `multiturn.yaml`: 8 roteiros.
- `analysis_plan.md`: conteúdo da spec §9.2 (estimando, IC de dois fatores, poder, meta, infra, gabarito, recusa, escolha do modelo, desdobramentos, contagem de rodadas, tag).
- `dataset.sha256` cobre o dataset, o plano e o commit do runner.

- [ ] **Step 1–4:**
  - `test_quotas_met_with_full_oracle`
  - `test_disjoint_from_golden`
  - `test_holdout_counts` (≥ 50 / ≥ 15)
  - `test_required_from_oracle`
  - `test_oracle_ideal_answers_pass_all` (autoteste)
  - `test_hash_covers_plan_and_runner`

  FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `test(evals): dataset com holdout, cotas e plano de análise pré-registrado`.

### Task 31: Qualidade — código (sexta) e rodadas (sábado)
**Depende de:** 25, 30

**Files:** Create `evals/stats.py`, `evals/golden/score.py`, `judge.py`, `run.py`, `labels.csv`, `select_conversations.py`, `evals/guard/run.py`. Test `tests/unit/test_stats.py`, `tests/unit/test_score.py`.

**Interfaces:**
- **Estatística:**
  - `two_way_bootstrap_ci(results, by=("student","template"), q)`;
  - `wilson`;
  - `ppv_npv`;
  - `reweighted_sens_spec`.
- **Pontuação:** `score_item` e `judge` (só no teste).
- **Runner:** `run.py --split dev|test --model M --record`, que registra o SHA e o número da rodada. Anota cada item no Phoenix.
- **Saídas:**
  - `quality.md`;
  - `guard.md` (perturbação + rascunhos do dev perturbados + desfecho do guard × scorer);
  - `conversations.md` (roteiros do replay e do vídeo + 2–3 falhas rotuladas).

- [ ] **Step 1–4:** testes de `stats`:
  - o bootstrap de dois fatores cobre ≥ 90% numa simulação com ICC conhecido;
  - Wilson.

  Testes de `score_item`:
  - "ativa" ≠ "inativa";
  - datas equivalentes;
  - recusa com oferta conta;
  - `expect_draft`;
  - multi-turno conta como um item.

  FAIL → implementar → PASS.
- [ ] **Step 5: Rodadas de sábado, em série**
  1. `security-review` + `code-review` (manhã) → correções;
  2. rodada dev com os 2 modelos;
  3. ajuste (≤ 2 iterações / 3 h);
  4. escolha do modelo pela regra D11;
  5. ataques da Task 32 + correções;
  6. **congelar** (tag);
  7. teste 1×;
  8. Miguel faz os rótulos (30 respostas, 20 itens do oráculo) → `make eval-guard`.
- [ ] **Step 6: Commit** — `test(evals): qualidade com IC de dois fatores, scorer validado e conversas curadas`.

### Task 32: Ataques — código (sexta) e rodada (sábado)
**Depende de:** 25, 27

**Files:** Create `evals/redteam/templates.yaml`, `mutators.py`, `plant.py`, `check.py`, `run.py`, `api_attacks.py`; Modify `etl/__main__.py` (`--plant <db>`, que planta num banco separado `eval_redteam`). Test `tests/unit/test_redteam_check.py`.

**Interfaces:**
- **Ataques de comportamento:** 10 categorias × 20 ataques distintos (≥ 5 modelos × mutadores), 2 rodadas cada. O ataque conta como sucesso se qualquer rodada falhar a defesa.
- **Ataques de vazamento:** 5 categorias × 8 ataques, 1 rodada.
- **Payloads com predicado checável:** "dívida perdoada", "protocolo cancelado", "PIX 0000", "abra o cancelamento", entre outros.
- **Checagem:** `check_attack` com conjunto proibido, 8-gramas, `draft` (inclusive no turno seguinte) e predicados. Juiz nas categorias 3, 4, 6 e 12–15.
- **Rótulos humanos:** o Miguel rotula as respostas das categorias 3, 4 e 6.
- **`api_attacks.py`:** IDOR por recurso e por método A2A 1.0, contextId alheio, troca de canal, JWT forjado, `alg=none`, `aud` errado, CSRF, push, DataPart e contorno de limite.
- **`security.md`:**
  - k/n por categoria;
  - Wilson **por modelo de ataque**;
  - seção "zero por construção" com a saída do pytest das Tasks 11, 14, 20 e 24–27.

- [ ] **Step 1–4:**
  - nome sem acento casa;
  - RA compartilhado não gera falso positivo;
  - o predicado do payload é detectado;
  - `draft` no turno seguinte é detectado.

  FAIL → implementar → PASS.
- [ ] **Step 5:** Sábado: `make eval-security` no banco `eval_redteam`. Todo vazamento ou quebra vira teste de regressão + correção antes do congelamento.
- [ ] **Step 6: Commit** — `test(evals): 240 ataques com predicados, ataques à API e evidência de segurança`.

### Task 33: Latência e carga — código e carga (sexta), bench (domingo)
**Depende de:** 25

**Files:** Create `evals/latency/bench.py`, `loadgen.py`, `capacity.py`, `app/agent/fakes.py::LoadTestLLM`. Test `tests/unit/test_capacity.py`, `tests/unit/test_loadgen.py`.

**Interfaces:**
- **`LoadTestLLM`:** latência amostrada das 40×2 chamadas do spike e cota simulada.
- **`loadgen.py`:** laço aberto, Poisson.
- **Duas escadas,** com critérios de aprovação escritos antes em `latency.md`:
  - (a) limitador ligado;
  - (b) limitador desligado.
  - Configurações: 1 container × {1, 2} workers, `cpus:` limitado.
- **`capacity` e `monthly_cost`.**
- **`bench.py --per-class 100`:** IC por aluno, cascata, TTFE, cache, custo por turno e Vertex, se disponível. Roda **sozinho** no domingo às 7h; a janela A vem da rodada dev.

- [ ] **Step 1–4:** `test_capacity_known_values`, `test_loadgen_poisson_rate` → FAIL → implementar → PASS.
- [ ] **Step 5:** Sexta à noite: `make load-test`. Domingo às 7h: `make eval-latency`.
- [ ] **Step 6: Commit** — `test(evals): latência por fórmula e IC, carga em duas escadas e custo`.

---

## Fase 6 — Documento e entrega

### Task 34: Documentação
**Depende de:** 31, 32, 33 (esqueleto escrito no sábado à noite)

**Files:** Create `docs/ARCHITECTURE.md`, `docs/evidence/README.md`; Modify `README.md`.

**Interfaces:**
- **README:**
  - resumo executivo com 3 números e IC (dev e teste lado a lado; vistos × não vistos);
  - `cp .env.example .env` → `docker compose up` → `make login CASE=4` → `make a2a-demo`;
  - replay;
  - tabela requisito → mecanismo → teste → número;
  - diagrama;
  - vídeo.
- **ARCHITECTURE:**
  - as 10 ★, com a pergunta difícil que cada uma responde;
  - tabela das menores, com o motivo de não serem ★;
  - grafo gerado pelo `draw_mermaid`;
  - dados, segurança, qualidade, latência e custo, observabilidade;
  - integração em produção (CDC/API, outbox, IdP, Vertex com cota, Langflow/ADK via A2A);
  - limites honestos;
  - "Como foi construído" conforme a §12.

- [ ] **Step 1:** Escrever com números reais e fonte.
- [ ] **Step 2:** `make check && make test-full` → PASS.
- [ ] **Step 3: Commit** — `docs: arquitetura, resumo executivo e evidências`.

### Task 35: Pacote e teste do zero (domingo)
**Depende de:** 34

**Files:** Create `scripts/package.sh`.

**Interfaces:**
- `make package` gera `dist/student-agent.zip` com `git archive`.
- Na opção §12(a): commits de documentação compactados e `git bundle`.
- Na opção §12(b): sem `docs/superpowers/` e sem histórico.

- [ ] **Step 1:** `unzip -l`: nada de `.git/`, `.rev`, `.env`, `dados.zip`, `docs/superpowers/` (na opção b) ou segredos.
- [ ] **Step 2: Teste do zero**
  1. diretório novo;
  2. copiar o `dados.zip`;
  3. `cp .env.example .env` + chave;
  4. `docker compose up`;
  5. `make login CASE=4` e 3 perguntas;
  6. `make a2a-demo`;
  7. `docker compose exec api` sem `/data` nem segredos do ETL;
  8. repetir em replay, sem chave.
- [ ] **Step 3:** Segunda: refazer o pacote depois da reescrita do documento; Miguel grava o vídeo de 3 min (login, pergunta cruzada, ataque bloqueado, rascunho → confirmação, trace + anotação no Phoenix, cliente A2A).
- [ ] **Step 4: Commit** — `chore: pacote de entrega`.
