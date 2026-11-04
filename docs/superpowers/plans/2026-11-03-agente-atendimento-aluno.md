# Agente de atendimento ao aluno — plano de implementação (v3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agente que atende o aluno autenticado (acadêmico, financeiro, protocolos, LMS, catálogo) com isolamento garantido pela arquitetura, respostas presas à evidência e evidências medidas de qualidade, segurança, latência e observabilidade.

**Architecture:**
- **ETL:** resolve as entidades dos 4 sistemas num Postgres canônico com RLS forçado e sem dado interno.
- **API:** FastAPI stateless; a identidade vem só do JWT.
- **Turno:** um `TurnService` comum ao chat SSE e ao A2A monta o cartão do aluno e roda um `StateGraph` do LangGraph sobre o `google-genai`.
- **Ferramentas:** tipadas, sem parâmetro de identidade.
- **Saída:** um EvidenceGuard determinístico checa toda resposta.

**Tech Stack:** Python 3.12, uv, FastAPI, psycopg 3 + psycopg_pool, Postgres 17, Redis 7, `google-genai` 2.34, `langgraph` 1.2, `a2a-sdk[http-server]` 1.3, `jsonschema`, `pyjwt`, `structlog`, `opentelemetry-sdk` + `openinference-semantic-conventions` (Phoenix no compose), `prometheus-client`, pytest (+ pytest-asyncio, fakeredis, hypothesis), ruff, mypy.

**Spec:** [docs/superpowers/specs/2026-11-03-agente-atendimento-aluno-design.md](../specs/2026-11-03-agente-atendimento-aluno-design.md) (v3) · Pesquisa: [docs/research/2026-11-03-perfil-dos-dados.md](../../research/2026-11-03-perfil-dos-dados.md) · Case: `data/case/README.md` (fora do git)

## Global Constraints

- **Commits:** autor `Miguel Sanz <miguelsanzb.p@gmail.com>` (config local do repo), sem trailers de coautoria. Conventional Commits em PT-BR. Conferir com `git log -1 --format='%an <%ae>%n%b'`.
- **Código e dados:** nenhum código de outro projeto copiado; `data/` nunca entra no git; segredos nunca no git (são gerados por `init-secrets`).
- **Idioma:** identificadores de código em inglês. Prompt, respostas, chaves dos dados que o LLM lê e docs em PT-BR.
- **Relógio:** só em `core/clock.py`. `reference_today(settings) -> date` (negócio, `2026-10-31`) e `wall_now() -> datetime` (operação: JWT, rate limit, `created_at`). Teste proíbe `now()`, `CURRENT_DATE`, `CURRENT_TIMESTAMP`, `date.today`, `datetime.now`, `datetime.utcnow` e `time.time` fora desse módulo, inclusive no SQL. Datas de calendário são `date`; instantes são `timestamptz`, exibidos em America/Sao_Paulo.
- **Ferramentas:**
  - nenhum parâmetro de identidade;
  - `enrollment` ∈ `{"1","2","3"}`, mapeado no servidor;
  - schema com `additionalProperties: false`, passado via `parameters_json_schema`;
  - declarações e prefixo do system prompt **idênticos para todos os alunos**;
  - ≤ 20 itens + `total` por lista;
  - vazio → `"Nenhum registro encontrado."`.
- **Formatos:** `R$ 1.234,56`; `dd/mm/aaaa`; nota com uma casa decimal.
- **Limites (perfil `default`):**
  - entrada ≤ 2000 caracteres;
  - 20 mensagens/min por aluno;
  - 200.000 tokens/dia por aluno;
  - teto global de US$ 20/dia (degrada para flash-lite MINIMAL a 80%, 503 a 100%);
  - histórico de 5 turnos, TTL 1800 s;
  - JWT HS256 de 30 min;
  - rascunho com TTL de 600 s;
  - grafo ≤ 4 rodadas, orçamento de 20 s, `recursion_limit = 2·rounds + 6`.
  - Perfil `eval` com limites próprios: turno bloqueado por limite = inválido.
- **Banco:**
  - schemas `canon` (recriado pelo ETL) e `agent_rw` (persistente);
  - roles `etl_owner` (NOSUPERUSER) e `agent_app` (NOBYPASSRLS);
  - `FORCE ROW LEVEL SECURITY`;
  - a API aborta se a própria role for superuser ou bypassrls, ou se algum segredo for de exemplo;
  - a API nunca recebe segredos do ETL nem monta `data/`.
- **Escrita:**
  - `AGENT_SERVICES = {DOC-001, DOC-002, DOC-003, DOC-040, DOC-041, ACA-011, ACA-020, ACA-021, FIN-030, FIN-031, FIN-032, TEC-060, GEN-090, OUV-099}`;
  - elegibilidade por regra;
  - só no canal web, sem taint;
  - token nunca no contexto do LLM.
- **Lint:** ruff (linha 100); `mypy --strict` em `core/`, `etl/`, `app/guards/`, `app/rules/`.
- **Revisão:**
  - **por tarefa** nas tarefas de segurança: 9, 12, 16, 18, 19, 20, 22, 23, 24, 25;
  - **por fase** nas demais;
  - implementadores de tarefas mecânicas (3, 5, 21, 26, 31) podem usar modelo mais barato;
  - `ponytail-review` e `pytest-coverage` ao fim de cada fase;
  - `security-review` + `code-review` na Task 32.
- **Skills:** sempre `superpowers:test-driven-development` e `superpowers:verification-before-completion`; `fastapi-expert` nas Tasks 22–27.

## Review Focus

1. **Aluno com matrícula ativa + formada pergunta sobre diploma ou dívida.** A resposta diz de qual matrícula fala e oferece a outra; o financeiro sem filtro traz a quebra + total. Testes nas Tasks 15, 14 e 20.
2. **Protocolo de outro aluno** recebe a mesma mensagem de inexistente. Teste na Task 16.
3. **Banca roda depois de 10/11:** resposta idêntica. Testes nas Tasks 1, 13 e 15.
4. **Gemini 429/5xx/timeout ou cota esgotada:** nova tentativa só se houver tempo. Depois, evento `error` em PT-BR sem stack trace, com métrica. Também via A2A. Testes nas Tasks 2, 23 e 25.
5. **Pergunta fora da janela dos dados ou domínio vazio:** responde "não há dados antes de dd/mm/aaaa" ou "Nenhum registro encontrado.", nunca "0". Testes nas Tasks 15, 17 e 20.

---

## Mapa de arquivos

| Caminho | Responsabilidade |
|---|---|
| `pyproject.toml`, `uv.lock`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `.gitattributes`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md` | Projeto e regras |
| `scripts/init_secrets.py`, `ops/postgres/init.sh`, `ops/prometheus/prometheus.yml` | Segredos, roles, scrape |
| `core/normalize.py`, `format.py`, `keys.py`, `clock.py`, `ontology.py`, `scope.py` | Compartilhado |
| `ontology/entities.yaml`, `enums.yaml`, `tables.yaml`, `coverage.yaml`, `tools/*.yaml`, `subject_aliases.yaml` | Ontologia |
| `etl/config.py`, `raw.py`, `resolve_core.py`, `resolve_lms.py`, `resolve_secretary.py`, `build.py`, `schema.sql`, `rls.sql`, `load.py`, `report.py`, `__main__.py` | ETL |
| `app/config.py` | `AppSettings` |
| `app/data/db.py`, `app/data/card.py`, `app/data/queries/*.py` | Pool, escopo, cartão, consultas |
| `app/rules/finance.py`, `academic.py`, `lms.py`, `protocols.py`, `eligibility.py`, `coverage.py` | Regras puras |
| `app/tools/base.py`, `registry.py`, `academic.py`, `finance.py`, `protocols.py`, `lms.py`, `catalog.py` | Ferramentas |
| `app/infra/redis_stores.py` | Rate limit, orçamento, teto, RPM, histórico, rascunhos, TaskStore A2A |
| `app/agent/llm.py`, `gemini.py`, `fakes.py`, `replay.py`, `prompt.py`, `messages.py`, `graph.py` | Agente |
| `app/guards/input.py`, `evidence.py` | Guards |
| `app/service/turn.py` | TurnService |
| `app/api/auth.py`, `dev.py`, `chat.py`, `protocols.py`, `feedback.py`, `a2a.py`, `app/main.py` | API |
| `app/obs/logging.py`, `tracing.py`, `metrics.py`, `cost.py` | Observabilidade |
| `app/web/*` | Chat web |
| `scripts/make_fixture.py`, `gen_subject_aliases.py`, `a2a_client_demo.py`, `package.sh` | Utilitários |
| `evals/oracle.py`, `tokens.py`, `client.py`, `stats.py`, `golden/*`, `guard/*`, `redteam/*`, `latency/*` | Avaliação |
| `tests/unit`, `tests/integration`, `tests/e2e`, `tests/live`, `tests/fixtures/mini`, `tests/adversarial.py` | Testes |
| `docs/ARCHITECTURE.md`, `docs/SLO.md`, `docs/evidence/*.md` | Documentação |

## Interfaces centrais

```python
# core/scope.py
@dataclass(frozen=True)
class EnrollmentRef:
    index: str            # "1" | "2" | "3" (ordem: principal primeiro, depois status_date desc)
    enrollment_key: UUID
    label: str            # "ADS EAD - UniSaoPaulo (ativa)"
    brand: str            # código SAG
    brand_family: str     # sem "_EAD"
    status: str
    is_primary: bool

@dataclass(frozen=True)
class StudentScope:
    person_key: UUID
    first_name: str
    enrollments: tuple[EnrollmentRef, ...]     # ≤ 3
    brand_families: tuple[str, ...]

# app/data/card.py
@dataclass(frozen=True)
class StudentCard:
    text: str                       # ≤ 400 tokens, PT-BR, marcado como contexto de sistema
    facts: dict[str, str]           # ex.: {"1.total_para_quitar": "R$ 49.508,43", "1.provas_pendentes": "0"}

# app/tools/base.py
@dataclass(frozen=True)
class ToolSpec: name: str; description: str; parameters: dict[str, Any]
@dataclass(frozen=True)
class DraftCard:
    draft_id: str; service_code: str; service_name_pt: str; enrollment_label: str
    fee: str; due_date: str; description: str; warning: str
@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, Any]            # o que o LLM vê (chaves PT)
    untrusted: bool = False         # algum campo free_text presente
    draft: DraftCard | None = None
@dataclass(frozen=True)
class ToolContext:
    scope: StudentScope
    settings: AppSettings
    channel: Literal["web", "a2a"]
    db: Callable[[], AbstractAsyncContextManager[AsyncConnection]]
    drafts: DraftStore
Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutput]]
@dataclass(frozen=True)
class Tool: spec: ToolSpec; handler: Handler; read_only: bool = True

# app/agent/llm.py
@dataclass(frozen=True)
class ToolCall: id: str; name: str; args: dict[str, Any]; provider_meta: bytes | None = None
@dataclass(frozen=True)
class Usage: input_tokens: int = 0; output_tokens: int = 0; thinking_tokens: int = 0; cached_tokens: int = 0
@dataclass(frozen=True)
class LLMResponse:
    text: str | None; tool_calls: tuple[ToolCall, ...]; usage: Usage
    finish_reason: str = "STOP"; provider_meta: bytes | None = None
@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool", "system_note"]
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None
    untrusted: bool = False
    provider_meta: bytes | None = None
    turn: int = 0
class LLMProvider(Protocol):
    async def generate(self, *, system: str, messages: Sequence[Message],
                       tools: Sequence[ToolSpec], deadline: float) -> LLMResponse: ...
class LLMUnavailable(Exception): reason: str   # "timeout" | "quota" | "queue" | "server"

# app/agent/graph.py
@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: Literal["ok", "degraded", "fallback", "budget_exceeded", "llm_unavailable", "blocked_limit"]
    guard: Literal["pass", "retry_pass", "degraded", "fallback", "n/a"]
    draft: DraftCard | None
    tool_calls: tuple[str, ...]
    llm_calls: int
    usage: Usage
    timings_ms: dict[str, float]
    new_messages: tuple[Message, ...]

# app/service/turn.py
class TurnService:
    async def handle(self, *, scope: StudentScope, conversation_id: str, text: str,
                     channel: Literal["web", "a2a"], emit: Callable[[dict], Awaitable[None]]) -> TurnResult: ...
```

## Raias, estimativas e marcos

Estimativas P50 / P90 de relógio, com implementador e revisor:

| Tarefa | P50 | P90 | | Tarefa | P50 | P90 |
|---|---|---|---|---|---|---|
| 1 Base | 1,25 | 2 | | 18 Cartão + exposição + LLM comprometido | 1,25 | 2 |
| 2 Spikes + provider | 2 | 3 | | 19 Guards + perturbação | 1,75 | 2,5 |
| 3 Normalização | 0,5 | 0,75 | | 20 Prompt + grafo | 2 | 3 |
| 4 Raw + fixture + oráculo | 1,75 | 2,5 | | 21 Replay | 0,5 | 0,75 |
| 5 Ontologia | 1,25 | 2 | | 22 Auth + dev login | 1 | 1,5 |
| 6 Resolução core | 0,75 | 1 | | 23 TurnService + SSE | 1,5 | 2,5 |
| 7 Resolução LMS | 0,75 | 1 | | 24 Confirmação, feedback, CSRF/IDOR | 0,75 | 1,25 |
| 8 Resolução secretaria | 0,75 | 1,25 | | 25 A2A | 1,5 | 2,5 |
| 9 Schema + RLS | 1 | 1,5 | | 26 Web | 0,75 | 1 |
| 10 Build | 1,75 | 2,5 | | 27 Observabilidade | 1,25 | 2 |
| 11 Carga + relatório | 1 | 1,5 | | 28 Dataset + plano de análise | 2 | 3 |
| 12 Escopo + base | 1 | 1,5 | | 29 Runner + juiz (+1,5 h de rodada) | 2 | 3 |
| 13 Regras | 1 | 1,5 | | 30 Ataques (+1 h de rodada) | 1,75 | 2,5 |
| 14 Acadêmico | 1 | 1,5 | | 31 Latência e carga (+1 h de rodada) | 1,5 | 2,5 |
| 15 Financeiro | 1,25 | 2 | | 32 Docs + revisões | 2,5 | 3,5 |
| 16 Protocolos | 1,5 | 2 | | 33 Pacote + teste do zero | 1 | 1,5 |
| 17 LMS + catálogo | 1 | 1,5 | | | | |

**Raias:**
- **Dia 1:** T1 → (T2 ∥ T3 → T6 → (T7 ∥ T8)) · T4 · T5 → T9. Depois T10 → T11.
- **Dia 2:** (T12 ∥ T13) → (T14 ∥ T15) e (T16 ∥ T17) em 2 worktrees → T18. Em paralelo: T19 → T20 → T21. Depois T22 → T23.
- **Dia 3 (tarde):** T24 ∥ T25 ∥ T26 ∥ T27.
- **Dia 4:** T28 → (T29 ∥ T30 ∥ T31).
- **Dia 5:** rodadas; T32; T33.

**Caminho crítico:** P50 ≈ 26 h, P90 ≈ 37 h. A janela vai de qua 9h a dom 18h, sem a sexta de manhã.

**Merges sem conflito:**
- `registry.py` nasce na T12 já com os 11 nomes e handlers stub, e cada domínio troca só o próprio stub;
- saídas declaradas em `ontology/tools/<domínio>.yaml`;
- `fakes.py` pertence à T2, e `tests/adversarial.py` à T18.

**Pré-requisito do Miguel (terça à noite):** chave paga do Gemini em `.env` (AI Studio) ou projeto GCP (Vertex).

**Portões do Miguel:** só nos 4 marcos. A execução segue se não houver objeção em 1 h.

| Marco | Critério | Se falhar |
|---|---|---|
| **M1 · qua 22h** | `data-quality.md` com igualdade por regra; `spikes.md` com portão de latência, cache entre alunos, posse do A2A e stream do LangGraph | Aliases saem do caminho crítico (teste com skip). Se o spike do A2A falhar, o A2A vira cartão + `message/send` com bearer. Se o portão de latência falhar, as metas da spec §1.3 são reescritas na hora |
| **M2 · qui 22h** | Chat ponta a ponta com Gemini real para os golden 1 e 4, com o cartão respondendo em 1 chamada | A T27 fica em logs + Prometheus (Phoenix depois); cobertura só em financeiro e LMS |
| **M3 · sex 22h** | Fase 4 fechada, `make check` verde | Carga até o 1º joelho; 2º modelo sai; ataques em 1 rodada |
| **M4 · sáb 22h** | `quality.md`, `guard.md`, `security.md` gerados | Bench com n = 100 no total; conversas mínimas (12) |
| **Dom 18h** | código congelado, pacote e teste do zero feitos | — |

**Nunca cortar:**
- isolamento (RLS, roles, JWT, ferramentas sem identidade, segredos gerados);
- EvidenceGuard com perturbação;
- escrita restrita;
- LLM comprometido;
- ataques (≥ 1 rodada);
- eval de teste (1 rodada no escolhido);
- latência real;
- `docker compose up` único;
- replay;
- A2A mínimo (cartão + `message/send` + bearer + posse);
- conversas curadas;
- resumo executivo e documento.

**Agenda humana:**
- ter: chave;
- qua: amostra de 20 aliases (opcional);
- sáb: 30 rótulos às cegas (~1 h);
- dom: ler o rascunho do documento;
- seg: reescrever trechos na própria voz, vídeo de 3 min, ensaiar as 10 decisões ★ e as 15 perguntas difíceis (fora do repo), envio;
- ter: buffer até 12h.

---

## Fase 0 — Base e spikes

### Task 1: Base, segredos gerados, compose, CI e relógio

**Files:**
- Create: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.dockerignore`, `.env.example`, `.gitattributes`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md`, `scripts/init_secrets.py`, `ops/postgres/init.sh`, `core/__init__.py`, `core/clock.py`, `app/__init__.py`, `app/config.py`, `etl/__init__.py`, `etl/config.py`, `tests/conftest.py`
- Test: `tests/unit/test_config.py`, `tests/unit/test_clock_rule.py`, `tests/unit/test_init_secrets.py`

**Interfaces:**
- Produces: `init_secrets(root: Path) -> None`, idempotente.
  - Cria `root/app/{jwt_secret,dev_login_secret,prompt_canary,redis_password,agent_app_password}`, `root/etl/{person_key_secret,etl_owner_password,agent_app_password}` e `root/db/{etl_owner_password,agent_app_password}` com `secrets.token_urlsafe(48)` (o canário com `token_hex(8)`).
  - Não sobrescreve o que já existe. Permissão 0400.
- Produces: `AppSettings`, com `secrets_dir="/run/secrets/app"` e `.env` só para valores não secretos e para `GEMINI_API_KEY`. Campos:
  - **ambiente e conexões:** `app_env: Literal["dev","prod"]="dev"`, `db_host`, `db_name`, `redis_host`;
  - **segredos:** `jwt_secret`, `dev_login_secret`, `prompt_canary`, `redis_password`, `agent_app_password`;
  - **sessão e tempo:** `public_origin`, `dev_login=False`, `golden_login_path=Path("/run/etl-out/golden_login.json")`, `reference_date=date(2026,10,31)`, `timezone`;
  - **LLM:** `llm_provider`, `llm_model="gemini-3.5-flash-lite"`, `llm_fallback_model="gemini-3.5-flash-lite"`, `llm_thinking_level="MINIMAL"`, `llm_timeout_s=8.0`, `llm_rpm_limit=600`, `llm_max_concurrency=16`, `llm_queue_max=64`, `max_output_tokens=800`, `judge_model="gemini-4-argon"`, `gemini_api_key`;
  - **limites:** `limits_profile: Literal["default","eval"]`, mais os da Global Constraints;
  - **pool e observabilidade:** `pool_max_size=10`, `otel_endpoint`, `otel_capture_content=False`.
  - Validadores:
    - recusa segredo vazio, < 24 caracteres, ou contendo `change-me`/`example`;
    - `otel_capture_content=True` exige `app_env="dev"`.
- Produces: `EtlSettings` (`secrets_dir="/run/secrets/etl"`).
- Produces: `core/clock.py` com `reference_today(settings) -> date` e `wall_now() -> datetime` (UTC).
- Produces: `docker-compose.yml`. Serviços:

  | Serviço | O que faz |
  |---|---|
  | `init-secrets` | one-shot; volumes `secrets-app`, `secrets-etl`, `secrets-db` |
  | `db` | postgres:17; `init.sh` lê `/run/secrets/db` e cria roles e schemas `canon`/`agent_rw` |
  | `redis` | `--requirepass` lido do segredo |
  | `etl` | one-shot; monta `data/` (ro), `secrets-etl`, `etl-out` e `eval-out` |
  | `api` | `secrets-app` + `etl-out` (ro); **sem** `data/`; porta `127.0.0.1:8000` |

  - Profile `obs`: `phoenix` (127.0.0.1:6006), `prometheus` (127.0.0.1:9090).
  - Profile `eval`: `evals` (monta `eval-out`, `secrets-app` (ro), `data/` (ro) para o oráculo).
- Produces: `.dockerignore` (`data/`, `.env`, `.git`, `docs/superpowers/`, `tests/fixtures/`); `.gitattributes` com `docs/superpowers/ export-ignore`.
- Produces: CI com serviço postgres:17. Um step `psql` cria roles e schemas com senhas geradas pelo próprio job; depois `make check`.
- Produces: Makefile com `up`, `down`, `test`, `test-full`, `test-live`, `lint`, `check`, `eval-quality`, `eval-guard`, `eval-security`, `eval-latency`, `load-test`, `package`.

- [ ] **Step 1: Write the failing tests**
  - `test_app_settings_defaults`.
  - `test_placeholder_secret_rejected` (`"change-me-please-0000000000"` → `ValidationError`).
  - `test_short_secret_rejected`.
  - `test_capture_content_requires_dev`.
  - `test_app_settings_has_no_etl_secrets` (sem `person_key_secret` nem `etl_owner_password`).
  - `test_init_secrets_random_idempotent_and_0400`.
  - `test_clock_only_in_core_clock`: regex `now\(\)|CURRENT_DATE|CURRENT_TIMESTAMP|date\.today|datetime\.now|datetime\.utcnow|time\.time\(` sobre `core/`, `app/`, `etl/`, `evals/` e `*.sql`, exceto `core/clock.py` e `evals/latency/` (medição de duração).
- [ ] **Step 2:** `uv run pytest tests/unit -v` → FAIL.
- [ ] **Step 3:** Implementar. `AGENTS.md` com as Global Constraints, comandos, layout e "dados em `data/`".
- [ ] **Step 4:** `make lint && uv run pytest tests/unit -v` → PASS; `docker compose config` válido; `docker compose up init-secrets db redis` sobe.
- [ ] **Step 5: Commit** — `chore: base com segredos gerados, compose, CI e relógio único`.

### Task 2: Provider Gemini, fakes e spikes ao vivo

**Files:**
- Create: `app/agent/llm.py`, `app/agent/gemini.py`, `app/agent/fakes.py`, `app/infra/redis_stores.py` (só `RpmLimiter`), `app/obs/tracing.py` (fachada no-op), `app/obs/cost.py`
- Test: `tests/unit/test_gemini_adapter.py`, `tests/unit/test_fakes.py`, `tests/unit/test_cost.py`, `tests/live/test_spikes.py` (`live`)
- Evidence: `docs/evidence/spikes.md`

**Interfaces:**
- Produces: tipos centrais.
- `GeminiProvider(settings, limiter: RpmLimiter)` com `generate(..., deadline)`:
  - `client.aio.models.generate_content`;
  - `http_options` com retry do SDK desligado;
  - `FunctionDeclaration(parameters_json_schema=...)`;
  - AFC desligado;
  - `ThinkingConfig(thinking_level=...)`;
  - `max_output_tokens`;
  - `thought_signature` ↔ `provider_meta`;
  - `usage_metadata` → `Usage`;
  - `finish_reason` repassado.
- Controle de vazão:
  - `asyncio.Semaphore(llm_max_concurrency)` com fila contada (> `llm_queue_max` → `LLMUnavailable("queue")`);
  - `RpmLimiter.acquire(deadline)`: token bucket no Redis, global entre réplicas.
- Retry:
  - só em 429/5xx/timeout;
  - só se `deadline - monotonic() > retry_floor_s` (p95 medido no spike, padrão 3 s);
  - respeita o `retryDelay` do `RetryInfo`;
  - no máximo 10% de retries por minuto (contador no Redis).
- Fakes:
  - `ScriptedLLM(responses)`;
  - `LoadTestLLM(latency_sampler, quota: TokenBucket)` (chama `get_financial_summary` e copia o valor; 429 quando a cota simulada estoura).
- `make_llm(settings)`.
- `cost_usd(usage, model) -> float`, com tabela datada: entrada, saída com thinking, cache.
- `tracing.span(name, **attrs)`: no-op até a T27.

- [ ] **Step 1: Write the failing tests** (stub do SDK)
  - `test_parameters_json_schema_used`.
  - `test_thought_signature_roundtrip`.
  - `test_usage_and_finish_reason`.
  - `test_retry_only_with_time_left`.
  - `test_retry_honors_retry_info`.
  - `test_queue_full_raises`.
  - `test_cost_per_model`.
  - `test_loadtest_llm_quota_429`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Spikes ao vivo** (`make test-live`) → `docs/evidence/spikes.md`:
  1. `models.list` contém `llm_model`, `judge_model` e `gemini-3.8-flash`. Se não contiver, registrar os IDs e ajustar o `.env.example`.
  2. Schema estrito com `enum` e `additionalProperties:false` aceito; 2 voltas de function calling com thought signature, sem 400.
  3. **Portão de latência:** 20 chamadas por modelo (flash-lite, 3.8-flash) com MINIMAL, usando um system prompt de ~1.500 tokens e 11 declarações. Registrar p50/p95 por chamada; se p50 > 1,3 s, propor novas metas para a spec §1.3 no M1.
  4. **Cache entre alunos:** duas requisições de alunos diferentes com o mesmo system prompt e as mesmas declarações. Registrar `cached_content_token_count` da 2ª.
  5. **`max_output_tokens`:** com MINIMAL e LOW, verificar se o thinking conta no limite; ajustar o padrão para não haver `MAX_TOKENS` em respostas de 6 linhas.
  6. **A2A hello:** `DefaultRequestHandler(executor eco, InMemoryTaskStore, card)` + `create_jsonrpc_routes(..., context_builder=` builder que põe o `user` a partir de um cabeçalho de teste `)` → usuário A cria a tarefa, usuário B faz `tasks/get` → não encontrado (confirma `resolve_user_scope`). Sem `push_config_store`, o `pushNotificationConfig/set` dá erro.
  7. **LangGraph:** grafo de 2 nós com `get_stream_writer` emitindo eventos e `asyncio.CancelledError` propagando ao cancelar.
- [ ] **Step 6: Commit** — `feat(agent): provider Gemini com deadline e cota, fakes e spikes ao vivo`.

---

## Fase 1 — ETL

### Task 3: Normalização, formatação e chaves [revisão por fase]

**Files:** Create `core/normalize.py`, `core/format.py`, `core/keys.py`. Test `tests/unit/test_normalize.py`, `test_format.py`, `test_keys.py`.

**Interfaces:**
- **Normalização:** `ra_check_digit`, `normalize_ra`, `normalize_academic_ref`, `normalize_cpf`, `lms_login`, `parse_epoch`, `parse_iso`, `parse_naive_local`, `parse_date` (`AAAA-MM-DD` e `dd/mm/aaaa`), `parse_brl`, `parse_bool`, `digits`.
- **Formatação:** `fmt_brl`, `fmt_date(date)` (sem fuso), `fmt_instant(datetime)` (Brasília), `fmt_grade`, `fmt_pct`.
- **Chaves:** `hmac_uuid(secret: bytes, value: str) -> UUID`; `brand_family(brand) -> str`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("base,dv", [("18001", "7"), ("18015", "7"), ("01253", "0"), ("18172", "2")])
def test_ra_check_digit(base, dv): assert ra_check_digit(base) == dv

@pytest.mark.parametrize("raw,ra", [("18001-7", "180017"), ("1253-0", "012530"), ("18015", "180157"),
                                    (" 01281-5 ", "012815"), ("012816", None), ("", None)])
def test_normalize_academic_ref(raw, ra): assert normalize_academic_ref(raw) == ra

def test_dates_not_shifted_and_instants_converted():
    assert fmt_date(date(2026, 11, 5)) == "05/11/2026"
    assert fmt_instant(datetime(2026, 11, 1, 2, 1, tzinfo=UTC)) == "31/10/2026 23:01"

def test_money_cpf_family():
    assert parse_brl("R$ 1.234,56") == 123456 and fmt_brl(4950843) == "R$ 49.508,43"
    assert normalize_cpf("8140522660") == "08140522660" and brand_family("UniSaoPaulo_EAD") == "UniSaoPaulo"

def test_hmac_uuid(): assert hmac_uuid(b"a" * 32, "x") == hmac_uuid(b"a" * 32, "x") != hmac_uuid(b"b" * 32, "x")
```

- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(core): normalização, formatação e chaves HMAC`.

### Task 4: Leitura, fixture e oráculo independente

**Files:** Create `etl/raw.py`, `scripts/make_fixture.py`, `tests/fixtures/mini/**`, `tests/fixtures/mini/golden.json`, `evals/oracle.py`. Test `tests/unit/test_raw.py`, `tests/unit/test_oracle.py`, `tests/integration/test_oracle_full.py` (`fulldata`).

**Interfaces:**
- `read_raw(source) -> RawTables`.
- `make_fixture.py` (com `python3 -I`):
  - os 10 golden;
  - **só os parceiros de colisão dos RAs golden** (~24 matrículas);
  - o irmão 180548;
  - toda linha ligada por PERSON_ID, RA (com e sem zeros), CPF, `customerId`, `contractId`, username, `userid` e Protocol;
  - linhas a descartar presentes;
  - catálogos inteiros;
  - total < 3 MB.
- `evals/oracle.py`: só `csv`, regras reescritas, **sem importar** `etl/` nem `app/`. Funções:
  - `oracle_link_protocols(raw) -> dict[str, tuple[brand, ra]]`: RA+CPF, responsável pagador, fallback CPF+marca; pula os 17 ambíguos e os marca;
  - `oracle_financial(raw, cpf, ref)`: por matrícula e total; principal vencido, encargos em aberto, total para quitar, faturas vencidas;
  - `oracle_snapshot_check(raw, ref) -> tuple[int, int]` (contratos que batem / total);
  - `oracle_courses`;
  - `oracle_forecast`;
  - `oracle_protocols(raw, cpf, ref)`, com prazo;
  - `oracle_lms(raw, brand, ra, ref)`: só inscrição `status=0`; tentativas e provas abertas/pendentes;
  - `oracle_enrollment_events`;
  - `oracle_term_registration`;
  - `oracle_catalog(raw, family)`: cursos, serviços, preços;
  - `coverage(raw) -> dict`.

- [ ] **Step 1: Write the failing tests**
  - `test_read_raw_47`.
  - `test_fixture_partners_only` (≤ 30 matrículas de colisão).
  - `test_oracle_golden4`: `total_para_quitar == "R$ 49.508,43"`, `principal_vencido == "R$ 42.636,26"`, `faturas_vencidas == 32`.
  - `test_oracle_golden1_attempts == 7`.
  - Em `fulldata`:
    - `oracle_snapshot_check == (273, 273)`;
    - protocolos ligados = 3882 (3455/73/337/17);
    - `lms` "encerrada sem tentativa" só de inscrição ativa (≠ 743).
- [ ] **Step 2–4:** FAIL → implementar e gerar a fixture → PASS.
- [ ] **Step 5: Commit** — `feat(evals): leitura, fixture dos golden e oráculo independente com fallback`.

### Task 5: Ontologia [revisão por fase]

**Files:** Create `ontology/entities.yaml`, `enums.yaml`, `tables.yaml`, `coverage.yaml`, `ontology/tools/` (vazio), `subject_aliases.yaml`, `core/ontology.py`, `scripts/gen_subject_aliases.py`. Test `tests/unit/test_ontology.py`.

**Interfaces:**
- `Ontology.load(dir)` com os métodos:
  - `.sensitivity(entity, field)` e `.is_free_text(entity, field)`;
  - `.label_pt(enum, value)`;
  - `.table_destination(key) -> Literal["loaded","partial","resolution_only","discarded"]` e `.reason(key)`;
  - `.coverage(system) -> date`;
  - `.tool_outputs(tool) -> dict[str, FieldSpec]`;
  - `.service_name_pt`, `.course_name_pt`, `.subject_aliases`;
  - `get_ontology()`.
- `free_text: true` em: mensagens de protocolo, descrição de solicitação, nome de sala e de prova do LMS.
- `coverage.yaml`: `sag: 2021-02-01`, `secretary: 2022-03-01`, `lms: 2023-01-15`, `finance: <competência mínima calculada>`.
- **Aliases** saem por script uma vez, com amostra de 20 conferida. Ficam fora do caminho crítico: o teste deles tem skip se o arquivo estiver vazio.

- [ ] **Step 1–4:**
  - Testes:
    - `test_47_tables_have_destination_and_reason`;
    - `test_every_enum_value_in_fixture_has_pt_label`;
    - `test_internal_fields_marked`;
    - `test_free_text_fields_marked`;
    - `test_services_courses_pt` (`ACA-010` → "Trancamento de matrícula"; `210` → "Direito");
    - `test_coverage_dates`;
    - `test_subject_aliases_cover_all` (skip se vazio).
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: ontologia com rótulos PT, texto livre, cobertura e destino das 47 tabelas`.

### Task 6: Resolução — pessoas, matrículas, contratos

**Files:** Create `etl/resolve_core.py`. Test `tests/unit/test_resolve_core.py`.

**Interfaces:** `Person`, `Enrollment` (+ `brand_family`), `resolve_people(raw, secret)`, `resolve_enrollments(raw, people, secret)`, `brand_for_company`, `resolve_contracts(raw, enrollments) -> (dict, ResolutionStats)`, `ResolutionStats(by_rule: Counter, unresolved: list)`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_same_cpf_two_person_ids_one_person`;
    - `test_colliding_ra_distinct_enrollments`;
    - `test_contract_academic_ref_variants`;
    - `test_contract_never_by_payer_tax_id`;
    - `test_unresolved_reported`;
    - `test_first_name_title_case`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução de pessoas, matrículas e contratos`.

### Task 7: Resolução — LMS

**Files:** Create `etl/resolve_lms.py`. Test `tests/unit/test_resolve_lms.py`.

**Interfaces:** `resolve_lms_accounts(raw, enrollments) -> (dict, ResolutionStats)`, `is_student_login(v)` (`^(PRES|EAD)_\d{5}$` após trim/upper), `tenant_brand`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_sso_trim_upper`;
    - `test_same_login_other_tenant`;
    - `test_manual_by_login_event`;
    - `test_non_students_dropped` (`prof40013`, `aluno.teste3`, `admin`, `integration.sag`);
    - `test_ambiguous_manual_not_linked`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução das contas LMS`.

### Task 8: Resolução — protocolos

**Files:** Create `etl/resolve_secretary.py`. Test `tests/unit/test_resolve_secretary.py`.

**Interfaces:**
- `unit_brand_map`;
- `resolve_protocols(raw, people, enrollments, contracts) -> (dict, ResolutionStats)`, com as regras `ra_cpf`, `guardian_payer`, `cpf_brand_unique` e `cpf_brand_by_date`;
- `protocol_number`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_stu_id_without_leading_zeros`;
    - `test_typo_stu_id_by_cpf`;
    - `test_cpf_mismatch_not_by_ra`;
    - `test_guardian_payer_with_stu_id_linked`;
    - `test_guardian_not_payer_not_linked`;
    - `test_guardian_without_stu_id_not_linked`;
    - `test_third_party_not_linked`;
    - `test_two_enrollments_same_brand_by_date`;
    - `test_invariant_requester_is_student_or_payer`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução dos protocolos com invariante de solicitante`.

### Task 9: Schemas, roles e RLS

**Files:** Create `etl/schema.sql`, `etl/rls.sql`, `tests/integration/conftest.py`. Test `tests/integration/test_rls.py`.

**Interfaces:**
- Tabelas da spec §5.2 nos schemas `canon` e `agent_rw`.
  - `agent_rw.protocol_request` com `CHECK (service_code IN (...AGENT_SERVICES))`, índice único parcial, sequence com `USAGE`.
  - RLS `person_key = nullif(current_setting('app.person_key', true), '')::uuid`; catálogo `brand_family = ANY(string_to_array(current_setting('app.brands', true), ','))`; `WITH CHECK` nas tabelas de escrita.
- Fixture `db_dsns`: banco `test_<uuid>` criado com `etl_owner`; aplica schema e RLS. As roles vêm do `init.sh` ou do step do CI.

- [ ] **Step 1: Write the failing tests**
  - `test_no_setting_nothing`.
  - `test_other_person_nothing`.
  - `test_catalog_other_family_hidden` (UniSaoPaulo não vê UniLitoral; UniSaoPaulo_EAD **vê** UniSaoPaulo presencial).
  - `test_insert_other_person_rejected`.
  - `test_check_rejects_non_agent_service`.
  - `test_roles_flags` (`etl_owner` e `agent_app` sem superuser/bypass).
  - `test_grants_imply_forced_rls` (toda tabela com SELECT para `agent_app` e coluna `person_key`/`brand_family` tem `relrowsecurity` e `relforcerowsecurity`; nenhuma view).
  - `test_canon_drop_preserves_agent_rw`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): schemas canon/agent_rw, roles sem superusuário e RLS forçado`.

### Task 10: Transformações por domínio

**Files:** Create `etl/build.py`. Test `tests/unit/test_build.py`.

**Interfaces:**
- `build_academic`: `enrollment` (com família), `enrollment_event`, `term_enrollment` (CANCELLED prevalece), `class_result`.
- `build_finance`.
- `build_protocols`: só mensagens públicas.
- `build_lms`: `lms_quiz` só de inscrições `status=0` e `role=student`.
- `build_catalog`.
- Todas no formato `(raw, resolved) -> Rows`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_internal_messages_dropped` (contagem = oráculo);
    - `test_no_internal_columns`;
    - `test_lms_quiz_active_enrollments_only` (fixture: inscrição suspensa não gera prova);
    - `test_enrollment_event_protocol_ref_golden3` (valor exato do `PROTOCOL_REF` do evento de transferência, lido do CSV no teste);
    - `test_due_date_is_date`;
    - `test_payer_type_golden4_guardian`;
    - `test_issued_documents_loaded`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): transformações por domínio sem dado interno`.

### Task 11: Carga, relatório e arquivos de login

**Files:** Create `etl/load.py`, `etl/report.py`, `etl/__main__.py`. Test `tests/integration/test_load.py`, `tests/integration/test_full_counts.py` (`fulldata`).

**Interfaces:**
- `load_all(raw, settings) -> LoadReport`: recria só `canon`, com COPY numa transação.
- `write_report`: `data-quality.md` com igualdade por regra, invariantes e `oracle_snapshot_check`.
- `write_golden_login(path)` (`etl-out`).
- `write_eval_students(path, labels)` (`eval-out`; rótulo → `person_key`, sem CPF; todos os 1000, com atributos de estrato).
- Fixture `loaded_db`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_load_matches_oracle`;
    - `test_schema_matches_ontology`;
    - `test_reload_idempotent_keeps_agent_rw`;
    - `test_login_files_have_no_cpf`.
    - Em `fulldata`:
      - `person=1000`, `enrollment=1037`;
      - contratos 1065;
      - protocolos **= 3882** com 3455/73/337/17;
      - LMS 1003;
      - eventos com `protocol_ref` 229;
      - snapshot 273/273.
  - FAIL → implementar → PASS; `docker compose up etl` gera o relatório.
- [ ] **Step 5: Commit** — `feat(etl): carga, relatório de qualidade e arquivos de login sem CPF`.
- [ ] **Step 6:** Revisão da fase 1 (`ponytail-review`, cobertura). **Marco M1.**

---

## Fase 2 — Dados e ferramentas

### Task 12: Escopo, conexão, base e registro

**Files:** Create `core/scope.py`, `app/data/db.py`, `app/tools/base.py`, `app/tools/registry.py`; Modify `app/infra/redis_stores.py` (`DraftStore`, `LabelCache`). Test `tests/unit/test_tool_base.py`, `tests/integration/test_scope.py`.

**Interfaces:**
- **Conexão:**
  - `create_pool(dsn, max_size)` com `reset` (`RESET ALL`);
  - `assert_safe_role(pool)`;
  - `scoped(pool, person_key, brand_families)`: `app.person_key` vem **sempre** do JWT.
- **Escopo:** `load_scope(pool, person_key) -> StudentScope | None`; `LabelCache` só para rótulos, TTL 300 s, sem papel na autorização.
- **Matrícula:** `enrollment_param() -> dict` (`{"type":"string","enum":["1","2","3"]}`); `resolve_enrollment(scope, index | None) -> EnrollmentRef` (inexistente → `ToolArgError("matrícula inexistente")`).
- **Validação:** `validate_args`.
- **Rascunho:** `DraftStore` com `put` (token), `take` (GETDEL) e `pending(person_key, conversation_id)`.
- **Registro:** `build_tools(channel) -> dict[str, Tool]` com os 11 nomes; `draft_protocol` fica fora quando `channel="a2a"`. Handlers stub levantam `NotImplementedError` até cada domínio.

- [ ] **Step 1–4:**
  - Testes:
    - `test_tool_declarations_identical_across_students`;
    - `test_index_resolution_and_nonexistent`;
    - `test_primary_first` (golden 9: "1" = 182915 ATIVA);
    - `test_extra_arg_rejected`;
    - `test_draft_single_use_owner_bound`;
    - `test_pool_reset`;
    - `test_assert_safe_role_rejects_superuser`;
    - `test_a2a_registry_has_no_draft`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: escopo, conexão com reset, registro e enum estável de matrícula`.

### Task 13: Regras puras

**Files:** Create `app/rules/finance.py`, `academic.py`, `lms.py`, `protocols.py`, `eligibility.py`, `coverage.py`. Test `tests/unit/test_rules.py`.

**Interfaces:**
- **Financeiro:** `invoice_balance`; `open_charges`; `financial_position(..., today) -> Position(overdue_principal, open_charges, total_due, upcoming, next_due: date | None, days_to_next_due: int | None, overdue_count)`.
- **Acadêmico e LMS:** `graduation_forecast`; `quiz_status`.
- **Protocolos:** `sla_due`; `protocol_status_pt`.
- **Elegibilidade:** `eligibility(service_code, enrollment: EnrollmentFacts) -> Eligible | NotEligible(reason_pt)`, com as regras da spec §5.5.
- **Cobertura:** `coverage_note(system, start: date | None, ontology) -> str | None`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_reversed_payment`;
    - `test_partial_two_parts`;
    - `test_renegotiated_excluded`;
    - `test_charges_already_paid` (PARTIAL `7067951`: 259 pagos, 348 em aberto);
    - `test_position_reference_date_only`;
    - `test_quiz_status_cases`;
    - `test_forecast`;
    - `test_eligibility_diploma_requires_graduated`;
    - `test_eligibility_second_call_requires_in_progress`;
    - `test_eligibility_slip_requires_open_invoice`;
    - `test_coverage_note_before_lms_start`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(rules): regras puras de saldo, encargos, prova, prazo, elegibilidade e cobertura`.

### Task 14: Ferramentas acadêmicas

**Files:** Create `app/data/queries/academic.py`, `app/tools/academic.py`, `ontology/tools/academic.yaml`; Modify `registry.py` (só os stubs deste domínio). Test `tests/integration/test_tools_academic.py`.

**Interfaces:** `get_academic_profile` (com documentos emitidos), `list_course_results` (lista completa quando há filtro de `term`/`subject`), `get_curriculum_progress`. A saída sempre traz `matricula` (rótulo) e `outras_matriculas` (rótulos).

- [ ] **Step 1–4:**
  - Testes contra o oráculo:
    - `test_profile_golden1`;
    - `test_golden9_profile_shows_diploma_of_graduated_enrollment`;
    - `test_status_history_golden3`;
    - `test_cancelled_overrides_registered`;
    - `test_subject_filter_pt` ("cálculo");
    - `test_attendance`;
    - `test_progress_forecast`;
    - `test_full_list_for_term`;
    - `test_sag_coverage_note`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): ferramentas acadêmicas`.

### Task 15: Ferramentas financeiras

**Files:** Create `app/data/queries/finance.py`, `app/tools/finance.py`, `ontology/tools/finance.yaml`; Modify `registry.py`. Test `tests/integration/test_tools_finance.py`.

**Interfaces:** `get_financial_summary` (sem `enrollment`: `por_matricula` + `total_geral`), `list_invoices`, `list_agreements`. As chaves de saída ficam em PT: `principal_vencido`, `encargos_em_aberto`, `total_para_quitar`, `a_vencer`, `proximo_vencimento`, `dias_ate_vencimento`, `faturas_vencidas`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_golden4_total_para_quitar == "R$ 49.508,43"`;
    - `test_no_sibling_invoices`;
    - `test_golden9_breakdown`;
    - `test_golden1_no_agreements`;
    - `test_golden6_active_agreement`;
    - `test_cap_20_with_total`;
    - `test_reference_date_changes_overdue`;
    - `test_finance_coverage_note`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): ferramentas financeiras com total para quitar`.

### Task 16: Protocolos e rascunho

**Files:** Create `app/data/queries/protocols.py`, `app/tools/protocols.py`, `ontology/tools/protocols.yaml`; Modify `registry.py`. Test `tests/integration/test_tools_protocols.py`.

**Interfaces:**
- `list_protocols` e `get_protocol`: `untrusted=True` quando há texto livre.
- `draft_protocol`:
  1. `eligibility` → `NotEligible` devolve `{"elegivel": False, "motivo": ...}`;
  2. elegível → `ToolOutput(data={"rascunho_exibido": True, "servico", "taxa", "prazo"}, draft=DraftCard)`.
  - Registra o rascunho pendente na conversa; o token **não** vai em `data`.
- `orientation(service_code)` para ACA-010/012/013/014: prazo, taxa e canal, mais a oferta de GEN-090.
- `PROTOCOL_NOT_FOUND`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_foreign_and_nonexistent_same`;
    - `test_internal_notes_absent`;
    - `test_guardian_protocol_without_identity`;
    - `test_sibling_absent`;
    - `test_protocol_invoice_link`;
    - `test_sla_due_golden3`;
    - `test_pending_requester_label`;
    - `test_untrusted_flag_on_messages`;
    - `test_draft_not_eligible_diploma_for_active`;
    - `test_draft_token_not_in_data`;
    - `test_sa_protocol_readable`;
    - `test_orientation_lock_offers_gen090`;
    - `test_secretary_coverage_note`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): protocolos e rascunho com elegibilidade`.

### Task 17: LMS e catálogo

**Files:** Create `app/data/queries/lms.py`, `catalog.py`, `app/tools/lms.py`, `catalog.py`, `ontology/tools/lms.yaml`, `catalog.yaml`; Modify `registry.py`. Test `tests/integration/test_tools_lms_catalog.py`.

**Interfaces:**
- `get_lms_activity`: `untrusted=True` (nomes de sala e prova).
- `get_catalog`, com o catálogo pela **família da marca**.

- [ ] **Step 1–4:**
  - Testes:
    - `test_golden1_7_attempts`;
    - `test_golden2_30_attempts`;
    - `test_pending_quizzes_vs_oracle`;
    - `test_no_lms_account`;
    - `test_lms_before_coverage_note`;
    - `test_accesses_in_period`;
    - `test_event_no_ip_url`;
    - `test_catalog_family_scoped` (EAD vê o presencial da mesma marca, não outra marca);
    - `test_price_direito_pt`;
    - `test_calendar_current_term_av_dates`;
    - `test_services_orientation_lock`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): LMS com provas de inscrição ativa e catálogo por família da marca`.

### Task 18: Cartão do aluno, exposição e LLM comprometido

**Files:** Create `app/data/card.py`, `tests/adversarial.py`, `tests/integration/test_card.py`, `tests/integration/test_tool_exposure.py`, `tests/integration/test_compromised_llm.py`.

**Interfaces:**
- `build_student_card(conn, scope, settings) -> StudentCard`: conteúdo da spec §6.2, ≤ 400 tokens (conta por `len(text)/4` com folga). Os `facts` usam chaves `"<índice>.<campo>"` e incluem `rascunho_pendente`.
- `AdversarialLLM`: toda ferramenta × todo índice de matrícula × todo `service_code` × protocolos de **outros** alunos × strings de hypothesis.

- [ ] **Step 1–4:**
  - `test_card_golden4_facts_match_oracle`.
  - `test_card_token_budget`.
  - `test_card_contains_coverage`.
  - `test_tool_outputs_within_declared_fields` (10 golden × ferramentas).
  - `test_compromised_llm_never_sees_other_person`: golden 1, 2 e 4; ~500 chamadas; toda chave/identificador conferido como dono.
  - `test_fuzzed_args` (hypothesis, 200).
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: cartão do aluno; test(security): exposição e LLM comprometido`.
- [ ] **Step 6:** Revisão da fase 2.

---

## Fase 3 — Agente (raia paralela à fase 2)

### Task 19: Guards e conjunto de perturbação

**Files:** Create `app/guards/input.py`, `app/guards/evidence.py`, `evals/guard/perturb.py`. Test `tests/unit/test_input_guard.py`, `tests/unit/test_evidence_guard.py`, `tests/unit/test_perturbation.py`.

**Interfaces:**
- **Entrada:** `normalize_input`.
- **Extração:** `extract_claims(text, vocabulary) -> list[Claim]`, com `kind` ∈ money, date, pct, decimal, int, id, cpf, cnpj, email, phone, status, polarity, action.
- **Checagem:** `check_evidence(answer, *, evidence: Sequence[str], card_facts: dict, user_text, canary, prompt_prefix, vocabulary) -> GuardResult(ok, ungrounded, prompt_leak)`. A evidência já vem **sem** campos `free_text`, filtrados pelo grafo com a ontologia.
- **Degradação:** `degrade(answer, ungrounded) -> str | None`, com aviso por tipo; `strip_markdown`.
- **Tabelas declaradas:**
  - `POLARITY_RULES` = [("em dia" | "sem débitos" | "não deve", "total_para_quitar == R$ 0,00"), ("nenhuma prova pendente", "provas_pendentes == 0"), ("nenhum protocolo aberto", "protocolos_abertos == 0")];
  - `ACTION_RULES` = ("abri" | "registrei" | "foi aberto" | "protocolo criado" ⇒ `SA-\d{4}-\d{6}` na evidência).
- **Status:** checado só em frase com `sua|seu|você`.
- **Perturbação:** `perturb.build(tool_outputs_golden) -> list[(answer, is_correct, kind)]` gera ~100 respostas corretas por modelo e mutações de 1 fato por tipo (inclusive inteiro ≤ 12, status, polaridade, ação); `perturb.score(results) -> dict[kind, {recall, fpr}]`.

- [ ] **Step 1: Write the failing tests**

```python
TOOL = ['{"total_para_quitar": "R$ 49.508,43", "proximo_vencimento": "05/11/2026", "faturas_vencidas": 32, '
        '"frequencia": "75%", "status": "ativa", "protocolo": "20260024855"}']
FACTS = {"1.total_para_quitar": "R$ 49.508,43", "1.provas_pendentes": "0", "1.protocolos_abertos": "1"}
def chk(a, user=""):
    return check_evidence(a, evidence=TOOL, card_facts=FACTS, user_text=user, canary="c4n4ry-xyz",
                          prompt_prefix=PREFIX, vocabulary=VOCAB)

def test_grounded(): assert chk("Você deve R$ 49.508,43 em 32 faturas; vence em 05/11.").ok
def test_written_date(): assert chk("Vence dia 5 de novembro.").ok and not chk("Vence dia 6 de novembro.").ok
def test_mid_int(): assert not chk("Você tem 31 faturas vencidas.").ok
def test_pct(): assert not chk("Sua frequência é 80%.").ok
def test_status_affirmative_only():
    assert not chk("Sua matrícula está trancada.").ok and chk("O trancamento é feito pelo portal.").ok
def test_polarity(): assert not chk("Você está em dia com as mensalidades.").ok and chk("Você não tem provas pendentes.").ok
def test_action_claim_requires_sa(): assert not chk("Pronto, abri sua solicitação.").ok
def test_user_text_ids_only():
    assert chk("Não encontrei o protocolo 20259999999.", user="e o 20259999999?").ok
    assert not chk("Seu saldo é R$ 0,00.", user="meu saldo é R$ 0,00?").ok
def test_no_substring(): assert not chk("Você deve R$ 42,00.").ok
def test_prompt_leak(): assert chk("c4n4ry-xyz").prompt_leak
def test_degrade_typed_notice():
    out = degrade("Você deve R$ 49.508,43. Vence dia 9/11.", [Claim("date", "9/11", "11-09")])
    assert out.startswith("Você deve R$ 49.508,43.") and "vencimento" in out
```

`test_perturbation_recall_money_dates_ids_ge_95` e `test_perturbation_fpr_le_5` sobre a fixture.

- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(guards): EvidenceGuard com polaridade, ação e conjunto de perturbação`.

### Task 20: Prompt e grafo

**Files:** Create `app/agent/prompt.py`, `app/agent/messages.py`, `app/agent/graph.py`. Test `tests/unit/test_prompt.py`, `tests/unit/test_graph.py`.

**Interfaces:**
- `SYSTEM_PREFIX`: regras da spec §6.2, com o canário.
- `card_message(card) -> Message` (`role="system_note"`, marcada como contexto, não instrução).
- `wrap_tool_result(name, data, untrusted)`.
- Mensagens fixas:
  - `FALLBACK_UNGROUNDED` (oferece GEN-090);
  - `DEGRADE_NOTICES` por tipo;
  - `FALLBACK_BUDGET`;
  - `FALLBACK_LLM_UNAVAILABLE`;
  - `TOOL_UNAVAILABLE`;
  - `DRAFT_PENDING_USE_BUTTON = "Para abrir, use o botão Confirmar no cartão."`;
  - `TAINT_NO_DRAFT = "Para abrir uma solicitação, peça de novo em uma nova conversa."`.
- `build_graph(llm, settings)`:
  - estado `AgentState`;
  - nós `call_model`, `run_tools`, `check_evidence`, `degrade`;
  - `recursion_limit = 2*rounds + 6`.
- `run_turn(graph, *, ctx, tools, card, history, user_text, deadline, writer) -> TurnResult`.
- **Taint** = alguma mensagem `untrusted` na janela ou no turno ⇒ a lista de ferramentas passada ao LLM perde `draft_protocol`. Um pedido de rascunho nessa condição vira `TAINT_NO_DRAFT`.
- **Evidência** = cartão + resultados **sem** `free_text` (via ontologia) + contexto.

- [ ] **Step 1–4:**
  - Testes com `ScriptedLLM`:
    - `test_card_answers_in_one_call`;
    - `test_tool_then_answer`;
    - `test_parallel_tools`;
    - `test_invalid_args_not_executed`;
    - `test_tool_exception_unavailable`;
    - `test_guard_retry_pass`;
    - `test_guard_degraded_typed`;
    - `test_guard_fallback_offers_gen090`;
    - `test_worst_path_within_recursion_limit` (4 rodadas + resposta + guard + retry + guard + degrade);
    - `test_budget_exceeded`;
    - `test_llm_unavailable`;
    - `test_taint_hides_draft_same_turn`;
    - `test_taint_persists_next_turn_while_in_window`;
    - `test_taint_clears_after_window`;
    - `test_free_text_not_evidence` (mensagem plantada "saldo R$ 0,00" → "Você deve R$ 0,00" bloqueado);
    - `test_thought_signature_preserved`;
    - `test_history_cut_by_turn`;
    - `test_progress_events`;
    - `test_system_prompt_and_tools_identical_across_students`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): grafo LangGraph com cartão, taint por janela e EvidenceGuard`.

### Task 21: Replay [revisão por fase]

**Files:** Create `app/agent/replay.py`. Test `tests/unit/test_replay.py`.

**Interfaces:** `RecordingLLM(inner, path)`, `ReplayLLM(path)` (`script_key = sha256(label|user_texts)`), `ReplayLLM.suggested(label)`, e `make_llm` aceita `replay`.

- [ ] **Step 1–4:** `test_record_then_replay`, `test_unknown_question_message` → FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): modo replay`.
- [ ] **Step 6:** Juntar as raias das fases 2 e 3; revisão da fase 3.

---

## Fase 4 — API, A2A, UI e observabilidade

### Task 22: Autenticação, login simulado e checagens de subida

**Files:** Create `app/api/auth.py`, `app/api/dev.py`, `app/main.py`. Test `tests/e2e/test_auth.py`.

**Interfaces:**
- **Tokens:** `issue_token(person_key, aud: Literal["chat","a2a"], settings)` usando `wall_now()`; `verify_token(token, aud, settings) -> UUID` (`algorithms=["HS256"]`, exige `exp`, `sub` e `aud`).
- **Dependências:** `get_person_cookie` / `get_person_bearer`.
- **Login simulado:** `/dev/login` só com `dev_login=True`; exige `dev_login_secret`; só os `person_key` do `golden_login.json`; o cookie sai com `aud=chat`.
- **Lifespan:** `assert_safe_role`, a checagem de segredos do settings e um ping do Redis com senha.

- [ ] **Step 1–4:**
  - Testes:
    - `test_tampered_401`;
    - `test_expired_401`;
    - `test_alg_none_401`;
    - `test_wrong_alg_401`;
    - `test_a2a_token_rejected_on_chat`;
    - `test_body_identity_ignored`;
    - `test_dev_login_disabled_404`;
    - `test_dev_login_wrong_secret_403`;
    - `test_dev_login_non_golden_403`;
    - `test_cookie_flags`;
    - `test_startup_aborts_superuser`;
    - `test_startup_aborts_placeholder_secret`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): JWT com aud por canal, login simulado restrito e checagens de subida`.

### Task 23: TurnService e chat SSE

**Files:** Create `app/service/turn.py`, `app/api/chat.py`; Modify `app/infra/redis_stores.py` (`rate_limit_hit`, `TokenBudget`, `GlobalSpend`, `HistoryStore`). Test `tests/unit/test_turn_service.py`, `tests/e2e/test_chat.py`.

**Interfaces:**
- `TurnService.handle(...)`, nesta ordem:
  1. `normalize_input`;
  2. rate limit → orçamento → teto global (≥ 80% troca para `llm_fallback_model` MINIMAL; ≥ 100% → `blocked_limit`);
  3. rascunho pendente + intenção de confirmação → `DRAFT_PENDING_USE_BUTTON` sem LLM;
  4. histórico, escopo e cartão;
  5. `run_turn` com `deadline = monotonic() + loop_budget_s`;
  6. histórico gravado com os flags `untrusted`;
  7. span e métricas.
- Perfil `eval`: limites próprios e `blocked_limit` marcado inválido.
- `POST /chat`: eventos `meta`, `progress`, `draft`, `answer`, `error`, `ping`; desconexão cancela.

- [ ] **Step 1–4:**
  - Testes:
    - `test_progress_then_answer`;
    - `test_identity_claim_ignored`;
    - `test_rate_limit_429`;
    - `test_budget_exhausted`;
    - `test_global_cap_degrades_then_503`;
    - `test_history_isolated`;
    - `test_llm_unavailable_error_no_traceback`;
    - `test_extra_body_422`;
    - `test_long_input_error`;
    - `test_disconnect_cancels`;
    - `test_markdown_stripped`;
    - `test_pending_draft_yes_uses_button_message`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(service): TurnService com limites, cartão e histórico; chat SSE`.

### Task 24: Confirmação, feedback, CSRF e IDOR

**Files:** Create `app/api/protocols.py`, `app/api/feedback.py`. Test `tests/e2e/test_confirm_feedback.py`.

**Interfaces:**
- `POST /protocols/confirm`:
  - checa `Origin` e JSON;
  - faz `take`;
  - revalida `service_code` ∈ AGENT_SERVICES e a elegibilidade;
  - INSERT em `agent_rw`;
  - grava `system_note` "Solicitação SA-… aberta em dd/mm/aaaa" no histórico;
  - em conflito do índice único → 409.
- `POST /feedback`.

- [ ] **Step 1–4:**
  - Testes:
    - `test_confirm_once_concurrent`;
    - `test_other_students_draft_409`;
    - `test_csrf_wrong_origin_403`;
    - `test_duplicate_open_409`;
    - `test_description_edited_saved`;
    - `test_confirm_note_in_history_grounds_action_claim`;
    - `test_feedback_isolated`;
    - `test_idor_conversation_feedback`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): confirmação de protocolo, feedback e proteções de CSRF/IDOR`.

### Task 25: A2A

**Files:** Create `app/api/a2a.py`, `scripts/a2a_client_demo.py`; Modify `app/main.py`, `app/infra/redis_stores.py` (`RedisTaskStore`). Test `tests/e2e/test_a2a.py`.

**Interfaces:**
- `build_agent_card(settings) -> AgentCard`:
  - skill de consulta e orientação;
  - segurança bearer;
  - `AgentCapabilities(streaming=False, push_notifications=False)`;
  - texto "solicitações exigem confirmação humana no portal".
- `JwtContextBuilder(ServerCallContextBuilder)`: bearer `aud=a2a` → `ServerCallContext.user`.
- `RedisTaskStore(TaskStore)`: chave `a2a:{person_key}:{task_id}`, TTL 3600 s, posse pelo `resolve_user_scope`.
- `StudentAgentExecutor.execute`:
  - exige exatamente 1 TextPart (DataPart → erro);
  - `TurnService.handle(channel="a2a")`.
- Montagem: `DefaultRequestHandler(executor, RedisTaskStore, card)` **sem** `push_config_store` + `create_jsonrpc_routes(handler, "/a2a", context_builder)` + `create_agent_card_routes(card)` + `add_a2a_routes_to_fastapi`.
- `a2a_client_demo.py`: lê o card, envia uma pergunta e imprime a resposta.

- [ ] **Step 1–4:**
  - Testes:
    - `test_card_served_push_false`;
    - `test_no_bearer_rejected`;
    - `test_chat_token_rejected_on_a2a`;
    - `test_answers_for_token_owner`;
    - `test_identity_in_text_ignored`;
    - `test_datapart_rejected`;
    - `test_no_draft_tool_over_a2a`;
    - `test_rate_limit_applies_over_a2a`.
    - `test_task_ownership_by_method`, parametrizado em `message/send` com `taskId` alheio, `tasks/get`, `tasks/cancel`, `tasks/resubscribe` e listagem → não encontrado.
    - `test_push_config_rejected`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): A2A de consulta com bearer, posse por método e mesmos limites do chat`.

### Task 26: Chat web [revisão por fase]

**Files:** Create `app/web/index.html`, `app.js`, `style.css`; Modify `app/main.py`. Test `tests/e2e/test_web.py`.

**Interfaces:**
- Progresso, cartão do rascunho (descrição editável + aviso fixo + Confirmar), 👍/👎, perguntas sugeridas no replay.
- Só `textContent`.
- CSP da spec §8.

- [ ] **Step 1–4:** `test_csp`, `test_js_no_innerhtml` → FAIL → implementar → PASS.
- [ ] **Step 5:** Teste manual com o golden 4: "quanto devo?", "quero a 2ª via do histórico", "sim" → mensagem do botão → Confirmar. Capturas em `docs/evidence/img/`.
- [ ] **Step 6: Commit** — `feat(web): chat em texto puro com cartão de confirmação`.

### Task 27: Observabilidade

**Files:** Modify `app/obs/tracing.py` (implementação real), `app/agent/gemini.py`, `app/agent/graph.py`, `app/data/db.py`, `app/service/turn.py`, `app/api/a2a.py`. Create `app/obs/logging.py`, `app/obs/metrics.py`, `ops/prometheus/prometheus.yml`, `docs/SLO.md`. Test `tests/unit/test_obs.py`, `tests/e2e/test_observability.py`.

**Interfaces:**
- **Tracing:** `setup_tracing(settings)` com OTLP → Phoenix. Spans `chat.turn`, `agent.call_model`, `agent.tool`, `db.query` e `agent.guard`.
  - Atributos OpenInference (`openinference.span.kind`: CHAIN/LLM/TOOL; `llm.model_name`; `llm.token_count.prompt`/`completion`) e `gen_ai.*`, mais `prompt.sha`, `tools.sha`, `gen_ai.response.model`, `guard.outcome`, `llm.calls`.
  - Conteúdo só com `otel_capture_content`.
  - `record_exception` desligado: exceção vira só o tipo.
- **Logs:** `configure_logging`, com middleware de `trace_id`.
- **Métricas:**
  - `agent_turn_seconds{tools_class,guard,model}`, com buckets 0,25/0,5/1/2/3/4/6/8/12/20;
  - `agent_llm_seconds`, `agent_llm_errors_total{kind}`, `agent_llm_queue`, `agent_llm_retries_total`;
  - `agent_tool_seconds{tool}`, `agent_guard_total{outcome}`, `agent_rounds`, `agent_sse_open`;
  - `agent_tokens_total{kind}`, `agent_cost_usd_total`, `agent_feedback_total{useful}`.
- **`SLO.md`:** SLOs por `tools_class`, erro < 1%, custo/dia e o alerta proposto (burn rate 14,4×/1 h e 6×/6 h), sem arquivo de regras.

- [ ] **Step 1–4:**
  - Testes:
    - `test_spans_no_content_by_default` (exportador em memória; nenhum atributo nem evento com o texto do aluno ou da resposta);
    - `test_exception_has_no_message`;
    - `test_capture_content_only_dev`;
    - `test_metrics_labels_and_buckets`;
    - `test_logs_no_pii`.
  - FAIL → implementar → PASS.
- [ ] **Step 5:** `docker compose --profile obs up`, uma conversa e capturas do trace (Phoenix) e do `/metrics` em `docs/evidence/observability.md`.
- [ ] **Step 6: Commit** — `feat(obs): traces OpenInference sem conteúdo, métricas e SLOs`.
- [ ] **Step 7:** Revisão da fase 4. **Marco M3.**

---

## Fase 5 — Evals (Gemini real; fora do CI)

### Task 28: Dataset, plano de análise e acesso das evals

**Files:** Create `evals/tokens.py`, `evals/client.py`, `evals/golden/templates.yaml`, `paraphrases.yaml`, `multiturn.yaml`, `build.py`, `analysis_plan.md`, `dataset.json`, `dataset.sha256`. Test `tests/unit/test_dataset.py`.

**Interfaces:**
- **Acesso:**
  - `tokens.mint(person_key, aud)`: lê o `jwt_secret` de `/run/secrets/app` (container `evals`);
  - `ChatClient(base_url, token)` com `ask`, `confirm` e `a2a_send`.
- **Seleção do teste:** `select_test_students(eval_students, seed=20261103) -> list[label]`, 40 alunos:
  - estratos modalidade × {ativo, inativo};
  - cotas ≥ 3 para: 2 matrículas, IN_COLLECTION, acordo, protocolo aberto, LMS manual, transferência, pagador responsável, pagador empresa;
  - exclui os golden.
- **Modelos de pergunta:**
  - `templates.yaml`: cada um com `split: shared|test_only` (30% `test_only`), `category`, `expected_tools`, `required` (expressões sobre o oráculo), `forbidden`, `expect_draft: bool | null`;
  - categorias da spec §9.2, incluindo `out_of_coverage`, `contradictory` e `protocol_open` negativos.
- **Paráfrases:** `paraphrases.yaml`, também com `test_only`.
- **Multi-turno:** `multiturn.yaml` com 8 roteiros, inclusive "sim" depois do cartão e taint entre turnos.
- **Plano de análise:** `analysis_plan.md` conforme a spec §9.2. O `dataset.sha256` cobre o dataset **e** o plano.

- [ ] **Step 1–4:**
  - Testes:
    - `test_quotas_met`;
    - `test_test_split_disjoint_from_golden`;
    - `test_holdout_templates_absent_from_dev`;
    - `test_required_from_oracle_formats`;
    - `test_hash_covers_plan`.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `test(evals): dataset estratificado com holdout de perguntas e plano de análise pré-registrado`.

### Task 29: Runner de qualidade, juiz, guard e conversas

**Files:** Create `evals/stats.py`, `evals/golden/score.py`, `judge.py`, `run.py`, `labels.csv`, `select_conversations.py`, `evals/guard/run.py`. Test `tests/unit/test_stats.py`, `tests/unit/test_score.py`.

**Interfaces:**
- **Estatística:** `wilson`, `cluster_bootstrap_ci(results_by_student, q)`, `cohen_kappa`, `sens_spec`.
- **Pontuação:**
  - `score_item(reply, item)`: tokens canônicos com fronteira; ferramentas do trace; `expect_draft`; recusa com próximo passo aceita; categoria de falha;
  - `judge(reply, item)`: rubrica binária com gabarito.
- **Runner:** `run.py --split dev|test --model M --runs N --record` → `quality.md`, que traz:
  - SHA;
  - quantas vezes o teste rodou;
  - pass@1 com IC por aluno;
  - k/n por categoria;
  - modelos vistos × não vistos;
  - recusa;
  - guard (desfecho × scorer, com o rascunho de antes do guard gravado);
  - juiz (sensibilidade, especificidade, kappa);
  - falhas por tipo com `trace_id`;
  - latência por classe (janela A).
- **Guard:** `evals/guard/run.py` (perturbação, sem LLM) → `guard.md`.
- **Conversas:** `select_conversations.py` pré-seleciona 30 candidatas por categoria; o Miguel escolhe 20 → `conversations.md`.

- [ ] **Step 1–4:**
  - Testes de `stats` com valores conhecidos.
  - Testes de `score_item`: "ativa" não casa em "inativa"; "05/11/2026" ≡ "5 de novembro de 2026"; recusa com GEN-090 conta como correta; `expect_draft=False` com evento `draft` → falha.
  - FAIL → implementar → PASS.
- [ ] **Step 5: Rodadas**
  1. dev nos 2 modelos (1 rodada);
  2. ajuste em no máximo 2 iterações ou 3 h;
  3. escolha do modelo pela regra D11;
  4. **teste 1 vez** no escolhido;
  5. Miguel rotula 30 respostas estratificadas às cegas;
  6. `make eval-guard`.

  Falhas viram regressão **no dev**.
- [ ] **Step 6: Commit** — `test(evals): qualidade com IC por aluno, juiz calibrado, guard medido e conversas curadas`.

### Task 30: Ataques

**Files:** Create `evals/redteam/templates.yaml`, `mutators.py`, `plant.py`, `check.py`, `run.py`, `api_attacks.py`. Test `tests/unit/test_redteam_check.py`.

**Interfaces:**
- **Geração:** `mutators.expand(templates) -> list[Attack]` com 8 ataques distintos × 15 categorias = 120, por paráfrase, tradução, codificação e enquadramento.
- **Injeções:** `plant.py` planta em mensagem pública do golden 3 e numa descrição `SA-`, só no banco de eval.
- **Conjunto proibido:** `forbidden_set(owner_conn, case)` com identificadores únicos de outras pessoas, analistas, outras famílias de marca, segredos e canário.
- **Checagem:** `check_attack(reply, events, forbidden, expect, prefix)` cobre vazamento, 8-grama, `draft` indevido (inclusive no turno seguinte) e fatos.
- **Juiz:** nas categorias 3, 4 e 6.
- **Ataques à API:** `api_attacks.py`:
  - IDOR por recurso e por método A2A;
  - JWT forjado, `alg=none` e `aud` trocado;
  - CSRF;
  - push;
  - DataPart;
  - contorno de rate limit.
- **Runner:** `run.py`:
  - 1 rodada em tudo e 3 nas categorias de vazamento;
  - gera `security.md` com k/n por categoria e agrupamento por modelo;
  - a seção "zero por construção" referencia os testes de arquitetura e traz a saída do pytest das Tasks 9, 12, 18 e 22–25.

- [ ] **Step 1–4:**
  - Testes:
    - nome sem acento casa;
    - RA compartilhado não gera falso positivo;
    - marcador do payload detectado;
    - `draft` no turno seguinte detectado.
  - FAIL → implementar → PASS.
- [ ] **Step 5:** `make eval-security`. Todo vazamento → regressão + correção antes do congelamento.
- [ ] **Step 6: Commit** — `test(evals): 120 ataques com LLM real, ataques à API e evidência de segurança`.

### Task 31: Latência, carga, capacidade e custo [revisão por fase]

**Files:** Create `evals/latency/bench.py`, `loadgen.py`, `capacity.py`. Test `tests/unit/test_capacity.py`, `tests/unit/test_loadgen.py`.

**Interfaces:**
- **Bench:** `bench.py --model M --per-class 100` → percentis com IC por aluno, cascata, 1º evento útil, tokens em cache e custo por turno.
- **Carga:** `loadgen.py --rate R --duration S` em laço aberto (Poisson, asyncio).
  - Servidor com `LLM_PROVIDER=fake` → `LoadTestLLM` com latência amostrada do bench e cota simulada.
  - Escada de taxa até o joelho; 2 × 4 workers; `cpus:` por container.
- **Capacidade e custo:** `capacity(...)` e `monthly_cost(...)`.
- **Saída:** `latency.md` com janela A (rodada de qualidade) e B (bench), joelho, overhead de infra, CPU por turno, espera no pool, atraso do loop, capacidade × cota, custo por aluno/mês (2026/2027) e a rodada Vertex, se houver.

- [ ] **Step 1–4:** `test_capacity_known_values`, `test_loadgen_poisson_rate` → FAIL → implementar → PASS.
- [ ] **Step 5:** `make eval-latency`, `make load-test`.
- [ ] **Step 6: Commit** — `test(evals): latência com IC, carga em laço aberto com cota simulada e custo`.

---

## Fase 6 — Documento, revisão e entrega

### Task 32: Documentação e revisões

**Files:** Create `docs/ARCHITECTURE.md`, `docs/evidence/README.md`; Modify `README.md`.

**Interfaces:**
- **README:** resumo executivo de 1 página com:
  - o que é;
  - **3 números com IC** (qualidade no teste, latência p50/p95 da classe principal, ataques com 0 vazamento + "zero por construção");
  - `cp .env.example .env` (só a chave) + `docker compose up`;
  - modo replay;
  - tabela requisito → mecanismo → teste → número;
  - diagrama;
  - vídeo.
- **ARCHITECTURE:**
  - 10 decisões ★ com trade-offs e "o que mediria em produção" (cada uma com a pergunta difícil que responde), mais a tabela das menores;
  - dados e resolução;
  - segurança (ameaça × defesa × prova);
  - qualidade;
  - latência, capacidade e custo;
  - observabilidade;
  - integração em produção (CDC/API, outbox para a secretaria, IdP, Vertex com cota provisionada, orquestradores como Langflow consumindo via A2A);
  - limites honestos;
  - seção "Como foi construído" **conforme a decisão do Miguel** (spec §12).

- [ ] **Step 1:** Escrever com números reais e fonte em `docs/evidence/` (esqueleto no sábado à noite, enquanto as evals rodam).
- [ ] **Step 2:** `security-review` + `code-review`; cada achado confirmado → teste + correção.
- [ ] **Step 3:** `make check && make test-full` → PASS.
- [ ] **Step 4: Commit** — `docs: arquitetura, resumo executivo e evidências`.

### Task 33: Pacote e teste do zero (domingo, antes do congelamento)

**Files:** Create `scripts/package.sh`.

**Interfaces:**
- `make package` → `dist/student-agent.zip` (`git archive`, sem `.git`, `data/`, `.env` nem `docs/superpowers/`).
- O `git bundle` só entra se o Miguel escolher a opção (a) da spec §12.

- [ ] **Step 1:** Conferir com `unzip -l` (nada de `.git/`, `.rev`, `.env`, `dados.zip`, `docs/superpowers/` ou segredos).
- [ ] **Step 2: Teste do zero** em diretório novo:
  1. descompactar;
  2. copiar o `dados.zip` para `data/`;
  3. `cp .env.example .env` (+ chave);
  4. `docker compose up`;
  5. login golden e 3 perguntas;
  6. `docker compose exec api sh -c 'ls /data; ls /run/secrets/etl'` → inexistentes;
  7. repetir com `LLM_PROVIDER=replay` e sem chave;
  8. `python scripts/a2a_client_demo.py`.
- [ ] **Step 3:** Miguel grava o vídeo de 3 min: login, pergunta cruzada, ataque bloqueado, rascunho → confirmação, trace no Phoenix, cliente A2A.
- [ ] **Step 4: Commit** — `chore: pacote de entrega`.
