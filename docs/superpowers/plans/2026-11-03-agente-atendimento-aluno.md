# Agente de atendimento ao aluno — plano de implementação (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agente que atende o aluno autenticado (acadêmico, financeiro, protocolos, LMS, catálogo) com isolamento por arquitetura, respostas presas à evidência e evidências medidas de qualidade, segurança, latência e observabilidade.

**Architecture:**
- O ETL resolve as entidades dos 4 sistemas num Postgres canônico com RLS forçado e sem dado interno.
- A FastAPI stateless recebe a identidade só do JWT assinado e roda um `StateGraph` do LangGraph sobre o `google-genai`. As ferramentas são tipadas e não têm parâmetro de identidade.
- Toda resposta passa por um EvidenceGuard determinístico. A saída vai por SSE (chat web) e por A2A (JSON-RPC), e tudo é observável por OTel/Phoenix e Prometheus/Grafana.

**Tech Stack:** Python 3.12, uv, FastAPI, psycopg 3 + psycopg_pool, Postgres 17, Redis 7, `google-genai` 2.34, `langgraph` 1.2, `a2a-sdk[http-server]` 1.3, `jsonschema`, `pyjwt`, `cryptography` (Fernet), `structlog`, `opentelemetry-sdk` + `arize-phoenix-otel`, `prometheus-client`, pytest (+ pytest-asyncio, fakeredis, hypothesis), ruff, mypy, locust.

**Spec:** [docs/superpowers/specs/2026-11-03-agente-atendimento-aluno-design.md](../specs/2026-11-03-agente-atendimento-aluno-design.md) (v2) · Pesquisa: [docs/research/2026-11-03-perfil-dos-dados.md](../../research/2026-11-03-perfil-dos-dados.md) · Case: `data/case/README.md` (fora do git)

## Global Constraints

- **Commits:** só com o autor `Miguel Sanz <miguelsanzb.p@gmail.com>` (já no `git config` local). **Proibido** `Co-Authored-By` e "Generated with Claude Code". Conventional Commits, descrição em PT-BR. Conferir com `git log -1 --format='%an <%ae>%n%b'` depois de cada commit.
- **Código e dados:** nenhum código de outro projeto copiado. `data/` nunca entra no git.
- **Idioma:** identificadores em inglês; prompt, respostas, mensagens ao aluno e documentação em PT-BR.
- **Data de referência:** `REFERENCE_DATE=2026-10-31` é o único "hoje". `now()`, `CURRENT_DATE`, `date.today` e `datetime.now` são proibidos em `core/`, `app/`, `etl/` e no SQL (teste da Task 1). Datas de calendário são `date`; instantes são `timestamptz`, convertidos para America/Sao_Paulo só na exibição.
- **Ferramentas:**
  - nenhum parâmetro de identidade;
  - `enrollment` = enum dos rótulos das matrículas do próprio aluno;
  - schema com `additionalProperties: false`, passado ao Gemini via `FunctionDeclaration.parameters_json_schema`;
  - no máximo 20 itens + `total` por lista;
  - vazio → `"Nenhum registro encontrado."`.
- **Formatos de saída:** `R$ 1.234,56`; `dd/mm/aaaa`; nota com uma casa decimal (`7,0`).
- **Limites:**
  - entrada ≤ 2000 caracteres;
  - 20 mensagens/min por aluno;
  - 200.000 tokens/dia por aluno;
  - teto global de US$ 20/dia (configurável);
  - histórico = últimos 5 turnos inteiros, TTL 1800 s;
  - JWT HS256 com expiração de 30 min;
  - rascunho com TTL de 600 s;
  - grafo com ≤ 4 rodadas e orçamento de 20 s;
  - `llm_timeout_s=8`, 1 nova tentativa com jitter em 429/5xx, `max_output_tokens=400`, `llm_max_concurrency=16` por worker.
- **Banco:**
  - roles `etl_owner` (NOSUPERUSER, dono do schema) e `agent_app` (NOBYPASSRLS);
  - `FORCE ROW LEVEL SECURITY` em toda tabela com `person_key` ou `brand`;
  - a API recusa subir se `rolsuper` ou `rolbypassrls` da sua role for verdadeiro;
  - a API nunca recebe o DSN do dono nem o `PERSON_KEY_SECRET`.
- **Chaves:** `person_key` = HMAC-SHA256(`PERSON_KEY_SECRET`, CPF) → UUID; `enrollment_key` = HMAC(segredo, `f"{brand}:{ra}"`) → UUID.
- **Escrita:** `AGENT_SERVICES = {DOC-001, DOC-002, DOC-003, DOC-040, DOC-041, ACA-020, ACA-021, FIN-030, TEC-060, GEN-090, OUV-099}`; o resto é só orientação. O token do rascunho nunca entra no contexto do LLM.
- **Lint:** `ruff check` + `ruff format --check` (linha 100); `mypy --strict` em `core/`, `etl/`, `app/guards/` e `app/rules/`; mypy normal no resto.
- **Skills:**
  - sempre: `superpowers:test-driven-development` e `superpowers:verification-before-completion`;
  - `fastapi-expert` nas Tasks 22–27;
  - `pytest-coverage` ao fechar cada fase;
  - revisão por tarefa (`requesting-code-review`), exceto nas tarefas marcadas **[revisão por fase]**;
  - `ponytail-review` ao fim de cada fase;
  - `security-review` e `code-review` na Task 32.

## Review Focus

1. **Aluno com duas matrículas pergunta "quanto devo?"** → quebra por matrícula + total geral. Em ação específica de matrícula (rascunho), o agente pergunta qual. Testes nas Tasks 15 e 16.
2. **Número de protocolo de outro aluno** → mesma mensagem de inexistente, sem oráculo de existência. Teste na Task 16.
3. **Banca roda depois de 10/11, com o relógio da máquina em outra data** → respostas idênticas, porque tudo usa `REFERENCE_DATE`. Testes nas Tasks 1, 13 e 15.
4. **Gemini 429/5xx/timeout** → 1 nova tentativa com jitter. Se falhar de novo: evento `error` em PT-BR, sem stack trace, com métrica. Testes nas Tasks 2 e 23.
5. **Domínio vazio** (sem conta LMS, sem acordo, sem protocolo) → "Nenhum registro encontrado.", sem invenção. Testes nas Tasks 15 e 17.

---

## Mapa de arquivos

| Caminho | Responsabilidade |
|---|---|
| `pyproject.toml`, `uv.lock`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.env.example`, `.gitattributes`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md` | Projeto, comandos, ambiente, CI, regras |
| `ops/postgres/init.sh` | Cria `etl_owner`, `agent_app` e o schema (roda como superuser só na inicialização do volume) |
| `ops/prometheus/*`, `ops/grafana/*` | Scrape, regras de SLO, painel provisionado |
| `core/normalize.py`, `core/format.py`, `core/ontology.py`, `core/scope.py`, `core/keys.py` | Compartilhado entre ETL e app |
| `ontology/entities.yaml`, `enums.yaml`, `tables.yaml`, `tools.yaml`, `subject_aliases.yaml` | Ontologia |
| `etl/config.py`, `etl/raw.py`, `etl/resolve_core.py`, `etl/resolve_lms.py`, `etl/resolve_secretary.py`, `etl/build.py`, `etl/schema.sql`, `etl/rls.sql`, `etl/load.py`, `etl/report.py`, `etl/__main__.py` | ETL |
| `app/config.py` | `AppSettings` |
| `app/data/db.py`, `app/data/queries/*.py` | Pool, escopo, consultas por domínio |
| `app/rules/finance.py`, `academic.py`, `lms.py`, `protocols.py` | Regras puras |
| `app/tools/base.py`, `registry.py`, `academic.py`, `finance.py`, `protocols.py`, `lms.py`, `catalog.py` | Ferramentas |
| `app/infra/redis_stores.py` | Rate limit, orçamento, histórico, rascunhos, cache de escopo |
| `app/agent/llm.py`, `gemini.py`, `fakes.py`, `replay.py`, `prompt.py`, `messages.py`, `graph.py` | Agente |
| `app/guards/input.py`, `app/guards/evidence.py` | Guards |
| `app/api/auth.py`, `dev.py`, `chat.py`, `protocols.py`, `feedback.py`, `a2a.py`, `app/main.py` | API |
| `app/obs/logging.py`, `tracing.py`, `metrics.py`, `cost.py`, `audit.py` | Observabilidade |
| `app/web/index.html`, `app.js`, `style.css` | Chat web |
| `scripts/make_fixture.py`, `scripts/gen_subject_aliases.py`, `scripts/inspect_turn.py`, `scripts/package.sh` | Utilitários |
| `evals/oracle.py`, `evals/client.py`, `evals/stats.py`, `evals/golden/*`, `evals/redteam/*`, `evals/latency/*` | Avaliação |
| `tests/unit/`, `tests/integration/`, `tests/e2e/`, `tests/live/`, `tests/fixtures/mini/` | Testes |
| `docs/ARCHITECTURE.md`, `docs/SLO.md`, `docs/evidence/*.md` | Documentação e evidências |

## Interfaces centrais (referência para todas as tarefas)

```python
# core/scope.py
@dataclass(frozen=True)
class EnrollmentRef:
    enrollment_key: UUID
    label: str            # "ADS EAD - UniSaoPaulo (ativa)"; repetido → sufixo " #2"
    brand: str            # código SAG, ex.: "UniSaoPaulo_EAD"
    status: str           # status SAG
    is_primary: bool      # ACTIVE com status_date mais recente; senão a mais recente

@dataclass(frozen=True)
class StudentScope:
    person_key: UUID
    first_name: str       # title case
    enrollments: tuple[EnrollmentRef, ...]
    brands: tuple[str, ...]

# app/tools/base.py
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str      # PT-BR
    parameters: dict[str, Any]   # JSON Schema (object, additionalProperties: false)

@dataclass(frozen=True)
class DraftCard:
    draft_id: str; service_code: str; service_name_pt: str; enrollment_label: str
    fee: str; due_date: str; description: str; warning: str

@dataclass(frozen=True)
class ToolOutput:
    data: dict[str, Any]          # o que o LLM vê
    draft: DraftCard | None = None

@dataclass(frozen=True)
class ToolContext:
    scope: StudentScope
    settings: AppSettings
    db: Callable[[], AbstractAsyncContextManager[AsyncConnection]]   # transação já com escopo
    drafts: DraftStore

Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutput]]

@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: Handler
    read_only: bool = True
    returns_untrusted: bool = False   # get_protocol, list_protocols → ativa o taint

# app/agent/llm.py
@dataclass(frozen=True)
class ToolCall: id: str; name: str; args: dict[str, Any]; provider_meta: bytes | None = None
@dataclass(frozen=True)
class Usage: input_tokens: int = 0; output_tokens: int = 0; thinking_tokens: int = 0; cached_tokens: int = 0
@dataclass(frozen=True)
class LLMResponse: text: str | None; tool_calls: tuple[ToolCall, ...]; usage: Usage; provider_meta: bytes | None = None
@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_result: dict[str, Any] | None = None
    provider_meta: bytes | None = None
    turn: int = 0                      # para cortar o histórico por turno inteiro

class LLMProvider(Protocol):
    async def generate(self, *, system: str, messages: Sequence[Message],
                       tools: Sequence[ToolSpec]) -> LLMResponse: ...

class LLMUnavailable(Exception): reason: str

# app/agent/graph.py
@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: Literal["ok", "degraded", "fallback", "budget_exceeded", "llm_unavailable"]
    guard: Literal["pass", "retry_pass", "degraded", "fallback", "n/a"]
    draft: DraftCard | None
    tool_calls: tuple[str, ...]
    usage: Usage
    timings_ms: dict[str, float]
    new_messages: tuple[Message, ...]
```

## Raias, marcos e tempo

As estimativas de tempo do agente já incluem o implementador e o revisor.

| Fase | Tarefas | Tempo de relógio | Raias paralelas |
|---|---|---|---|
| 0 Base | 1, 2 | ~1,5 h | 2 ∥ 3–5 |
| 1 ETL | 3–11 | ~6 h | A: 3→6→(7 ∥ 8) · B: 4 · C: 5→9 · depois 10→11 |
| 2 Dados e ferramentas | 12–18 | ~4,5 h | 12 ∥ 13 → (14 ∥ 15) e (16 ∥ 17) em 2 worktrees → 18 |
| 3 Agente | 19–21 | ~3,5 h (dentro da fase 2) | 19 começa após 3; 20 após 2 e 12 |
| 4 API, A2A e observabilidade | 22–27 | ~4,5 h | 22 → 23 → (24 ∥ 26), 25, 27 |
| 5 Evals | 28–31 | ~7 h (inclui ~3 h de rodadas reais) | 28 → (29 ∥ 30 ∥ 31) |
| 6 Documento e entrega | 32–33 | ~4 h + Miguel | — |

**Total: ~27–30 h de relógio de agente.**

**Pré-requisito do Miguel (terça à noite):** chave paga do Gemini no `.env` (AI Studio) ou projeto GCP com Vertex.

**Cronograma:**

| Dia | Trabalho | Marco (decisão) |
|---|---|---|
| Qua 04 | Tasks 1–2; fase 1 inteira | **22h:** `data-quality.md` com 1065 / 3882 / 1003 e smoke ao vivo do Gemini passando |
| Qui 05 | Fases 2 e 3; Tasks 22–23 | **22h:** ponta a ponta no chat com o Gemini real para os golden 1 e 4 |
| Sex 06 | Tarde: Tasks 24–27 (manhã indisponível) | **20h:** fase 4 fechada |
| Sáb 07 | Fase 5: construção e rodadas. Miguel rotula 30 respostas para o juiz (~1 h) | **20h:** `quality.md` e `security.md` gerados |
| Dom 08 | Task 31; Task 32 (rascunho do documento); teste do zero | **18h:** código congelado |
| Seg 09 | Miguel: lê e reescreve o documento na própria voz, grava o vídeo de 3 min, ensaia D1–D20; Task 33 | envio à noite |
| Ter 10 | Buffer | entrega até 12h |

**Ordem de corte** (aplicar ao perder um marco; nunca cortar os itens de "Nunca cortar"):
1. Painel do Grafana (fica Prometheus + Phoenix).
2. Rodada via Vertex.
3. Escada de carga além de 200 usuários.
4. Matriz de thinking `LOW`.
5. pass^3 no segundo modelo (fica 1 rodada para comparar).
6. Calibração do juiz com 30 rótulos (passa para 15).
7. Aliases PT das disciplinas (fica busca por código + nome EN).

**Nunca cortar:**
- isolamento (RLS, roles, JWT, ferramentas sem identidade);
- EvidenceGuard;
- escrita restrita;
- teste de LLM comprometido;
- suíte de ataques;
- eval de qualidade no conjunto de teste;
- latência real medida;
- `docker compose up` único;
- modo replay;
- documento e resumo executivo;
- A2A.

---

## Fase 0 — Base

### Task 1: Projeto, compose, CI, regras e configuração

**Files:**
- Create: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `.env.example`, `.gitattributes`, `.github/workflows/ci.yml`, `AGENTS.md`, `CLAUDE.md`, `ops/postgres/init.sh`, `core/__init__.py`, `app/__init__.py`, `app/config.py`, `etl/__init__.py`, `etl/config.py`, `tests/conftest.py`
- Test: `tests/unit/test_config.py`, `tests/unit/test_no_wall_clock.py`

**Interfaces:**
- Produces: `AppSettings` (pydantic-settings, `env_file=".env"`). Campos e valores padrão:
  - banco e cache: `app_database_url`, `redis_url`, `pool_max_size=20`;
  - sessão: `jwt_secret: SecretStr` (≥ 32 caracteres), `jwt_ttl_min=30`, `public_origin="http://localhost:8000"`;
  - login simulado: `dev_login=False`, `dev_login_secret: SecretStr | None`, `golden_login_path=Path("data/golden_login.json")`;
  - datas: `reference_date=date(2026,10,31)`, `timezone="America/Sao_Paulo"`;
  - LLM: `llm_provider: Literal["gemini","replay","fake"]="gemini"`, `llm_model="gemini-3.5-flash-lite"`, `llm_thinking_level: Literal["MINIMAL","LOW"]="MINIMAL"`, `llm_timeout_s=8.0`, `llm_max_retries=1`, `llm_max_concurrency=16`, `max_output_tokens=400`, `judge_model="gemini-4-argon"`, `gemini_api_key: SecretStr | None`, `fake_llm_latency_ms=0`, `replay_path=Path("evals/replays")`;
  - limites: `max_input_chars=2000`, `rate_limit_per_min=20`, `daily_token_budget=200_000`, `global_daily_usd_cap=20.0`, `history_max_turns=5`, `history_ttl_s=1800`, `draft_ttl_s=600`, `loop_max_rounds=4`, `loop_budget_s=20.0`;
  - segredos: `prompt_canary: str` (obrigatório, ≥ 12 caracteres), `audit_key: SecretStr` (Fernet, obrigatório), `audit_ttl_days=7`;
  - observabilidade: `otel_endpoint: str | None=None`.
  - `get_app_settings()` com cache.
- Produces: `EtlSettings`: `owner_database_url`, `person_key_secret: SecretStr` (≥ 32 caracteres), `source=Path("data/dados.zip")`, `golden_login_path`. Nenhum campo do ETL existe em `AppSettings`.
- Produces: `docker-compose.yml`:
  - `db` (postgres:17, `init.sh`, healthcheck);
  - `redis`;
  - `etl` (one-shot, mesma imagem, `python -m etl`, depende de `db` saudável);
  - `api` (depende de `etl: service_completed_successfully` e de `redis`; porta `127.0.0.1:8000:8000`; env só com variáveis do `AppSettings`);
  - profile `obs`: `phoenix`, `prometheus`, `grafana`;
  - profile `load`: `fake-gemini`, `locust`.
- Produces: `.gitattributes` com `docs/superpowers/ export-ignore`. Targets do `Makefile`: `up`, `down`, `test`, `test-full`, `test-live`, `lint`, `check`, `eval-quality`, `eval-security`, `eval-latency`, `load-test`, `package`.

- [ ] **Step 1: Write the failing tests**
  - `test_app_settings_defaults`: `reference_date`, `loop_max_rounds == 4`, `llm_timeout_s == 8.0`, `max_output_tokens == 400`.
  - `test_short_jwt_secret_rejected`.
  - `test_canary_required`.
  - `test_app_settings_has_no_etl_secrets`: `"person_key_secret" not in AppSettings.model_fields` e nenhum campo `owner_*`.
  - `test_no_wall_clock`: varre `core/`, `app/`, `etl/` e `*.sql` com a regex `now\(\)|CURRENT_DATE|CURRENT_TIMESTAMP|date\.today|datetime\.now|datetime\.utcnow`; zero ocorrências fora de `app/obs/` (onde o relógio mede duração e marca logs) e de `app/infra/redis_stores.py` (janela de rate limit).
- [ ] **Step 2:** `uv run pytest tests/unit -v` → FAIL.
- [ ] **Step 3:** Implementar os arquivos. `init.sh` cria `etl_owner` (LOGIN NOSUPERUSER, senha do env) e `agent_app` (LOGIN NOBYPASSRLS NOSUPERUSER), e o schema `app` com dono `etl_owner`. CI: serviços postgres:17 (com `init.sh`) e redis:7; roda `make check`. `AGENTS.md`: Global Constraints, comandos, layout e "dados em `data/`".
- [ ] **Step 4:** `make lint && uv run pytest tests/unit -v` → PASS; `docker compose config` → válido.
- [ ] **Step 5: Commit** — `chore: base do projeto, compose, CI e configuração separada de app e ETL`.

### Task 2: Contrato do LLM, provider Gemini e smoke ao vivo

**Files:**
- Create: `app/agent/llm.py`, `app/agent/gemini.py`, `app/agent/fakes.py`
- Test: `tests/unit/test_gemini_adapter.py`, `tests/unit/test_fakes.py`, `tests/live/test_gemini_live.py` (marca `live`)

**Interfaces:**
- Produces: tipos de "Interfaces centrais".
- `GeminiProvider(settings: AppSettings)`:
  - cria `genai.Client()` (AI Studio por `GEMINI_API_KEY`; Vertex se `GOOGLE_GENAI_USE_VERTEXAI=true`);
  - `generate` usa `client.aio.models.generate_content` com `GenerateContentConfig(system_instruction, tools=[Tool(function_declarations=[FunctionDeclaration(name, description, parameters_json_schema=...)])], automatic_function_calling=AutomaticFunctionCallingConfig(disable=True), thinking_config=ThinkingConfig(thinking_level=...), max_output_tokens=...)`;
  - `Part.thought_signature` ↔ `ToolCall.provider_meta` / `Message.provider_meta` (ida e volta);
  - `usage_metadata` → `Usage(prompt_token_count, candidates_token_count, thoughts_token_count, cached_content_token_count)`;
  - `asyncio.Semaphore(llm_max_concurrency)` por processo, com métrica de espera (gancho para a Task 27);
  - `asyncio.timeout(llm_timeout_s)`;
  - 1 nova tentativa com jitter (0,2–0,6 s) em 429/5xx/timeout;
  - erro final → `LLMUnavailable(reason)`.
- `ScriptedLLM(responses: Sequence[LLMResponse | Exception])` registra `calls`.
- `LoadTestLLM(latency_ms)`: chama `get_financial_summary` e depois responde copiando o 1º `R$` da saída.
- `make_llm(settings) -> LLMProvider` (o `replay` entra na Task 21).

- [ ] **Step 1: Write the failing tests** (o cliente do SDK é trocado por um stub)
  - `test_function_declaration_uses_parameters_json_schema`.
  - `test_thought_signature_roundtrip`: a `Part` de resposta com `thought_signature=b"sig"` vira `ToolCall.provider_meta == b"sig"`, e a próxima requisição reenvia a mesma assinatura na `Part` de function call.
  - `test_usage_mapping_includes_thinking_and_cache`.
  - `test_retry_once_on_429_then_ok`.
  - `test_retry_exhausted_raises_llm_unavailable`.
  - `test_timeout_raises_llm_unavailable`.
  - `test_scripted_llm_order`.
- [ ] **Step 2:** Rodar → FAIL.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** Rodar → PASS.
- [ ] **Step 5: Smoke ao vivo** — `make test-live`:
  - `test_models_available`: `client.models.list()` contém `llm_model` e `judge_model`. Se não contiver, registrar os IDs disponíveis e ajustar o `.env.example` com o Miguel.
  - `test_function_call_with_strict_schema`: schema com `additionalProperties: false` e `enum` aceito; o modelo chama a ferramenta.
  - `test_thought_signature_live`: 2 voltas de function calling sem erro 400.
  - `test_thinking_minimal_latency`: imprime a latência.
  - Saída em `docs/evidence/gemini-smoke.md`.
- [ ] **Step 6: Commit** — `feat(agent): contrato do LLM, provider Gemini e smoke ao vivo`.

---

## Fase 1 — ETL

### Task 3: Normalização e formatação [revisão por fase]

**Files:**
- Create: `core/normalize.py`, `core/format.py`, `core/keys.py`
- Test: `tests/unit/test_normalize.py`, `tests/unit/test_format.py`, `tests/unit/test_keys.py`

**Interfaces:**
- Produces (normalização):
  - `ra_check_digit(base5) -> str`;
  - `normalize_ra(raw) -> str | None`;
  - `normalize_academic_ref(raw) -> str | None`;
  - `normalize_cpf(raw) -> str | None`;
  - `lms_login(ra, modality: Literal["PRES","EAD"]) -> str`;
  - `parse_epoch(v) -> datetime | None` (0/vazio → None, UTC);
  - `parse_iso(v) -> datetime | None`;
  - `parse_naive_local(v) -> datetime | None`;
  - `parse_date(v) -> date | None` (aceita `AAAA-MM-DD` e `dd/mm/aaaa`);
  - `parse_brl(v) -> int`;
  - `parse_bool(v) -> bool`;
  - `digits(s) -> str`.
- Produces (formatação):
  - `fmt_brl(cents) -> str`;
  - `fmt_date(d: date) -> str` (sem conversão de fuso);
  - `fmt_instant(dt: datetime) -> str` (converte para America/Sao_Paulo, `dd/mm/aaaa hh:mm`);
  - `fmt_grade(x) -> str | None`;
  - `fmt_pct(x) -> str` (`"75%"`).
- Produces (chaves): `hmac_uuid(secret: bytes, value: str) -> UUID`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("base,dv", [("18001", "7"), ("18015", "7"), ("01253", "0"), ("18172", "2")])
def test_ra_check_digit(base, dv): assert ra_check_digit(base) == dv

@pytest.mark.parametrize("raw,ra", [("18001-7", "180017"), ("1253-0", "012530"), ("18015", "180157"),
                                    (" 01281-5 ", "012815"), ("012816", None), ("", None)])
def test_normalize_academic_ref(raw, ra): assert normalize_academic_ref(raw) == ra

def test_dates_are_not_shifted():
    assert fmt_date(date(2026, 11, 5)) == "05/11/2026"
    assert fmt_instant(datetime(2026, 11, 1, 2, 1, tzinfo=UTC)) == "31/10/2026 23:01"

def test_money_and_cpf():
    assert parse_brl("R$ 1.234,56") == 123456 and fmt_brl(4950843) == "R$ 49.508,43"
    assert normalize_cpf("8140522660") == "08140522660"

def test_hmac_uuid_deterministic_and_secret_dependent():
    assert hmac_uuid(b"a" * 32, "x") == hmac_uuid(b"a" * 32, "x") != hmac_uuid(b"b" * 32, "x")
```

- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(core): normalização, formatação e chaves HMAC`.

### Task 4: Leitura dos CSV, fixture e oráculo independente

**Files:**
- Create: `etl/raw.py`, `scripts/make_fixture.py`, `tests/fixtures/mini/**` (gerado e commitado), `tests/fixtures/mini/golden.json`, `evals/oracle.py`
- Test: `tests/unit/test_raw.py`, `tests/unit/test_oracle.py`

**Interfaces:**
- Produces: `RawTables = dict[str, list[dict[str, str]]]` com chave `"<sistema>/<tabela>"`; `read_raw(source: Path) -> RawTables` aceita zip ou diretório.
- Produces: `make_fixture.py <zip> <dir>`, rodado com `python3 -I`. Seleção **por chaves amplas**:
  - CPFs dos 10 golden (pesquisa §6);
  - todas as pessoas com RA igual em qualquer marca;
  - o irmão (RA 180548);
  - toda linha ligada por `PERSON_ID`, RA (com e sem zeros), CPF, `customerId`, `contractId`, username LMS, `userid`, `Protocol`;
  - linhas a descartar presentes de propósito (contas admin/teste/`prof\d+`, notas internas, protocolos de responsável sem `Stu_ID`);
  - catálogos inteiros.
- `golden.json` = `[{"case", "cpf", "enrollments":[{"brand","ra"}]}]`. Fixture < 3 MB.
- Produces: `evals/oracle.py` — consultas **independentes do ETL** sobre `RawTables`, usando só `csv` e regras escritas de novo:
  - `oracle_financial(raw, cpf, reference_date) -> dict` com `principal_vencido`, `encargos_em_aberto`, `total_para_quitar`, `faturas_vencidas` e quebra por matrícula;
  - `oracle_courses(raw, brand, ra) -> dict`;
  - `oracle_protocols(raw, cpf) -> list[str]`;
  - `oracle_lms(raw, brand, ra, reference_date) -> dict` (tentativas e provas abertas/pendentes);
  - `oracle_enrollment_events(raw, brand, ra) -> list[dict]`.
- O oráculo resolve identidade só pelo caminho mais simples e conservador (RA + marca + CPF). Os casos que exigem fallback ficam marcados `oracle_skip`.

- [ ] **Step 1: Write the failing tests**
  - `test_read_raw_from_dir` (47 chaves).
  - `test_fixture_has_collision_partners` (`012815` em ≥ 3 marcas).
  - `test_oracle_golden4_total` (fixture; `total_para_quitar == "R$ 49.508,43"`, `principal_vencido == "R$ 42.636,26"`, `faturas_vencidas == 32`).
  - `test_oracle_golden1_quiz_attempts == 7`.
- [ ] **Step 2–4:** FAIL → implementar e gerar a fixture → PASS.
- [ ] **Step 5: Commit** — `feat(etl): leitura dos CSV, fixture dos golden e oráculo independente`.

### Task 5: Ontologia

**Files:**
- Create: `ontology/entities.yaml`, `enums.yaml`, `tables.yaml`, `tools.yaml` (vazio, preenchido pelas Tasks 14–17), `subject_aliases.yaml`, `core/ontology.py`, `scripts/gen_subject_aliases.py`
- Test: `tests/unit/test_ontology.py`

**Interfaces:**
- Produces: `Ontology.load(dir) -> Ontology`; `.sensitivity(entity, field)`; `.label_pt(enum, value) -> str`; `.table_destination(key) -> Literal["loaded","resolution_only","discarded"]` com `.reason`; `.tool_outputs(tool) -> frozenset[str]`; `.service_name_pt(code)`; `.course_name_pt(course_code)`; `.subject_aliases(code) -> tuple[str, ...]`; `get_ontology()`.
- Conteúdo dos YAML:
  - `enums.yaml` tem rótulo PT para todo enum dos 4 sistemas (status de matrícula, rematrícula, resultado, contrato, fatura, pagamento, acordo, protocolo, resolução, motivos do `enrollment_status_history`, tipos de solicitante, ações do LMS);
  - os 18 serviços e os 8 cursos com `name_pt`;
  - `tables.yaml` com as 47 tabelas e o destino da spec §5.3.7.
- `gen_subject_aliases.py`: chama o Gemini uma vez com as 260 disciplinas e grava `subject_aliases.yaml` (nome PT + até 2 sinônimos). Miguel ou o revisor confere 20 por amostra.

- [ ] **Step 1: Write the failing tests**
  - `test_47_tables_have_destination` (compara com as chaves de `read_raw(fixture)`).
  - `test_every_enum_value_in_fixture_has_pt_label`.
  - `test_internal_fields_marked` (`cpf`, `transaction_ref`, `ip`, `is_internal_note`, `collection_stage` → `internal`).
  - `test_services_and_courses_have_pt_names` (`service_name_pt("ACA-010") == "Trancamento de matrícula"`, `course_name_pt("210") == "Direito"`).
  - `test_subject_aliases_cover_all_subjects`.
- [ ] **Step 2–4:** FAIL → implementar, rodar o script de aliases e revisar a amostra → PASS.
- [ ] **Step 5: Commit** — `feat: ontologia com rótulos PT, destino das 47 tabelas e aliases de disciplinas`.

### Task 6: Resolução — pessoas, matrículas, contratos

**Files:**
- Create: `etl/resolve_core.py`
- Test: `tests/unit/test_resolve_core.py`

**Interfaces:**
- Produces: `@dataclass Person(person_key, display_name, first_name, cpf)` (CPF só em memória); `@dataclass Enrollment(...)` (campos da spec §5.2 + `enrollment_key`).
- `resolve_people(raw, secret) -> dict[str, Person]`.
- `resolve_enrollments(raw, people, secret) -> list[Enrollment]`.
- `brand_for_company(company_code, business_area) -> str`.
- `resolve_contracts(raw, enrollments) -> tuple[dict[str, UUID], ResolutionStats]`.
- `@dataclass ResolutionStats(by_rule: Counter[str], unresolved: list[tuple[str, str]])`.

- [ ] **Step 1: Write the failing tests** (linhas inline)
  - `test_same_cpf_two_person_ids_is_one_person`.
  - `test_colliding_ra_distinct_enrollments` (`012815` em `UniAtlantico_EAD` e `UniSaoPaulo_EAD`).
  - `test_contract_academic_ref_variants`.
  - `test_contract_never_matched_by_payer_tax_id`.
  - `test_unresolved_contract_reported`.
  - `test_first_name_title_case` (`"ISIS LUISA" → "Isis"`).
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução de pessoas, matrículas e contratos`.

### Task 7: Resolução — LMS

**Files:**
- Create: `etl/resolve_lms.py`
- Test: `tests/unit/test_resolve_lms.py`

**Interfaces:**
- Produces: `resolve_lms_accounts(raw, enrollments) -> tuple[dict[str, UUID], ResolutionStats]`; `is_student_login(value: str) -> bool` (`^(PRES|EAD)_\d{5}$` após trim/upper); `tenant_brand(tenant_id, modality) -> str`.

- [ ] **Step 1: Write the failing tests**
  - `test_sso_account_trim_upper` (`" ead_01206"`).
  - `test_same_login_other_tenant_other_student`.
  - `test_manual_account_by_login_event`.
  - `test_non_students_dropped` (`admin`, `integration.sag`, `support.lms`, `aluno.teste3`, `prof40013`).
  - `test_ambiguous_manual_account_not_linked`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução das contas LMS`.

### Task 8: Resolução — protocolos

**Files:**
- Create: `etl/resolve_secretary.py`
- Test: `tests/unit/test_resolve_secretary.py`

**Interfaces:**
- Produces: `unit_brand_map(raw) -> dict[str, tuple[str, str]]`; `resolve_protocols(raw, people, enrollments) -> tuple[dict[str, UUID], ResolutionStats]`; `protocol_number(ref) -> str`.

- [ ] **Step 1: Write the failing tests**
  - `test_stu_id_without_leading_zeros`.
  - `test_typo_stu_id_resolved_by_cpf` (golden 8: `182203` → `182230`).
  - `test_cpf_mismatch_not_linked_by_ra`.
  - `test_guardian_with_valid_stu_id_linked`.
  - `test_guardian_without_stu_id_not_linked`.
  - `test_third_party_not_linked`.
  - `test_two_enrollments_same_brand_by_open_date`.
  - `test_protocol_number` (`"PRT-2022/0003006"` → `"20220003006"`).
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): resolução dos protocolos`.

### Task 9: Schema, roles e RLS

**Files:**
- Create: `etl/schema.sql`, `etl/rls.sql`, `tests/integration/conftest.py`
- Test: `tests/integration/test_rls.py`

**Interfaces:**
- Produces: as tabelas da spec §5.2 (nomes exatos), com colunas de dinheiro em `bigint` (centavos), datas `date` e instantes `timestamptz`.
  - `protocol_request(id text pk default 'SA-' || ..., person_key, enrollment_key, service_code, description, status, created_at)` com índice único parcial `(person_key, enrollment_key, service_code) WHERE status = 'Aberto'` e `GRANT USAGE` na sequence.
  - `turn_audit`: `agent_app` só tem INSERT.
- RLS: `USING (person_key = nullif(current_setting('app.person_key', true), '')::uuid)`; catálogo por marca `USING (brand = ANY(string_to_array(current_setting('app.brands', true), ',')))`; `WITH CHECK` igual nas tabelas com INSERT.
- Fixture pytest `db_dsns` cria um banco `test_<uuid>` com o dono `etl_owner` e aplica schema e RLS. As roles são criadas uma vez por cluster (`init.sh`), sem colisão entre execuções.

- [ ] **Step 1: Write the failing tests** (banco vazio + linhas sintéticas inseridas como dono)
  - `test_no_setting_returns_nothing`.
  - `test_other_person_returns_nothing`.
  - `test_catalog_other_brand_hidden`.
  - `test_insert_request_for_other_person_rejected`.
  - `test_app_role_flags` (`rolsuper=false`, `rolbypassrls=false` para `agent_app` **e** `etl_owner`).
  - `test_grants_imply_forced_rls`: toda tabela com SELECT para `agent_app` e coluna `person_key`/`brand` tem `relrowsecurity` e `relforcerowsecurity`; nenhuma view sem `security_invoker`.
  - `test_agent_cannot_read_audit`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): schema canônico, roles sem superusuário e RLS forçado`.

### Task 10: Transformações por domínio

**Files:**
- Create: `etl/build.py`
- Test: `tests/unit/test_build.py`

**Interfaces:**
- Consumes: Tasks 3–8.
- Produces: funções puras que devolvem linhas prontas para a carga (`dict[str, list[tuple]]` por tabela):
  - `build_academic(raw, resolved) -> Rows`: `enrollment`, `enrollment_event` (sem `CHANGED_BY`, motivo PT), `term_enrollment`, `class_result` (professor só nome; disciplina com `name_pt`);
  - `build_finance(raw, resolved) -> Rows`: `contract` (`payer_type`), `invoice`, `payment`, `late_charge`, `discount`, `agreement`, `agreement_invoice`;
  - `build_protocols(raw, resolved) -> Rows`: `protocol` (tipo de solicitante), `protocol_event` (sem `Changed_By`), `protocol_message` (só `Is_Internal_Note=False`, autor por tipo), `issued_document` (sem `Signed_By`);
  - `build_lms(raw, resolved) -> Rows`: `lms_account`; `lms_quiz` (provas das salas com `role=student` do aluno, `is_practice`); `lms_quiz_attempt`; `lms_event` (ação PT, sem IP/URL);
  - `build_catalog(raw, ontology) -> Rows`.
- Toda função recebe `resolved: Resolved` (mapas das Tasks 6–8) e descarta o que não resolveu.

- [ ] **Step 1: Write the failing tests** (na fixture)
  - `test_internal_messages_dropped` (contagem = interações públicas dos protocolos ligados, contadas pelo oráculo).
  - `test_no_internal_columns_in_rows` (nenhuma chave `internal` da ontologia).
  - `test_lms_quiz_scoped_to_student_rooms`.
  - `test_enrollment_event_has_protocol_ref` (golden 3: o evento de transferência traz `20260000610` ou o `PROTOCOL_REF` correspondente).
  - `test_due_date_is_date_type`.
  - `test_payer_type_guardian_for_golden4`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(etl): transformações por domínio sem dado interno`.

### Task 11: Carga, relatório e CLI

**Files:**
- Create: `etl/load.py`, `etl/report.py`, `etl/__main__.py`
- Test: `tests/integration/test_load.py`, `tests/integration/test_full_counts.py` (`fulldata`)

**Interfaces:**
- Produces:
  - `load_all(raw, settings: EtlSettings) -> LoadReport`: schema + RLS + COPY numa transação; idempotente (recria o schema);
  - `write_report(report, Path("docs/evidence/data-quality.md"))`;
  - `write_golden_login(settings)`: grava `data/golden_login.json` = `[{"label": "Golden 4 — responsável pagador", "person_key": "..."}]`, sem CPF;
  - fixture pytest `loaded_db` (fixture mini carregada; entrega `(owner_dsn, app_dsn)`);
  - CLI `python -m etl`.

- [ ] **Step 1: Write the failing tests**
  - `test_load_fixture_counts_match_oracle`.
  - `test_schema_matches_ontology`.
  - `test_reload_is_idempotent`.
  - `test_golden_login_has_no_cpf`.
  - Em `fulldata`: `person = 1000`, `enrollment = 1037`, contratos = 1065, protocolos ≥ 3882, matrículas com LMS = 1003, `enrollment_event` com `protocol_ref` = 229.
- [ ] **Step 2–4:** FAIL → implementar → PASS; `docker compose up db etl` gera `data-quality.md` com os números.
- [ ] **Step 5: Commit** — `feat(etl): carga, relatório de qualidade e login simulado dos golden` (inclui `docs/evidence/data-quality.md`).
- [ ] **Step 6:** `ponytail-review` da fase 1; `pytest-coverage`.

---

## Fase 2 — Dados e ferramentas

### Task 12: Escopo, conexão e base das ferramentas

**Files:**
- Create: `core/scope.py`, `app/data/db.py`, `app/tools/base.py`, `app/tools/registry.py`, `app/infra/redis_stores.py`
- Test: `tests/unit/test_tool_base.py`, `tests/integration/test_scope.py`

**Interfaces:**
- Produces:
  - `create_pool(dsn, max_size) -> AsyncConnectionPool` com `reset=` que roda `RESET ALL`;
  - `assert_safe_role(pool)`: levanta se `rolsuper` ou `rolbypassrls`;
  - `scoped(pool, scope)` (transação + `set_config(..., true)`);
  - `load_scope(pool, person_key) -> StudentScope | None`, com cache Redis de 300 s em `ScopeCache`;
  - `enrollment_schema(scope) -> dict`;
  - `resolve_enrollment(scope, label | None) -> EnrollmentRef`;
  - `validate_args(schema, args) -> dict` (levanta `ToolArgError`);
  - `DraftStore(redis, ttl_s)` com `put(person_key, card: DraftCard) -> str` (token) e `take(person_key, draft_id, token) -> DraftCard | None` (`GETDEL`);
  - `build_tools(scope) -> dict[str, Tool]`.

- [ ] **Step 1: Write the failing tests**
  - `test_enum_only_own_labels`.
  - `test_default_enrollment_is_primary` (golden 9 → `UniSaoPaulo 182915`).
  - `test_extra_arg_rejected`.
  - `test_foreign_label_rejected`.
  - `test_draft_single_use_and_owner_bound`.
  - `test_pool_connection_reset` (depois de `scoped`, nova conexão do pool → `current_setting('app.person_key', true)` vazio).
  - `test_assert_safe_role_rejects_superuser`.
  - `test_load_scope_golden3_two_enrollments`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat: escopo do aluno, pool com reset e base das ferramentas`.

### Task 13: Regras puras

**Files:**
- Create: `app/rules/finance.py`, `app/rules/academic.py`, `app/rules/lms.py`, `app/rules/protocols.py`
- Test: `tests/unit/test_rules.py`

**Interfaces:**
- Produces:
  - `invoice_balance(net_cents: int, payments: Sequence[Payment]) -> int`;
  - `open_charges(late_charges: Sequence[LateCharge], payments: Sequence[Payment]) -> int`;
  - `financial_position(invoices, payments, late_charges, reference_date) -> Position(principal_vencido, encargos_em_aberto, total_para_quitar, a_vencer, proximo_vencimento: date | None, dias_ate_vencimento: int | None, faturas_vencidas: int)`;
  - `graduation_forecast(current_term: str, pending_semesters: Sequence[int]) -> str` (ex. `"2028/1"`);
  - `quiz_status(timeopen, timeclose, attempts: int, reference_date) -> Literal["aberta","realizada","encerrada sem tentativa","futura"]`;
  - `sla_due(opened_at: datetime, sla_hours: int) -> datetime`;
  - `protocol_status_pt(status) -> str` ("Pending Requester" → "aguardando você").
- Os tipos de entrada são dataclasses mínimas no próprio módulo.

- [ ] **Step 1: Write the failing tests**
  - `test_reversed_payment_does_not_reduce_balance`.
  - `test_partial_paid_in_two_parts`.
  - `test_renegotiated_excluded`.
  - `test_charges_already_paid_not_counted` (fatura PARTIAL `7067951`, 2 lançamentos, o 1º pago).
  - `test_position_uses_reference_date_only` (mesma entrada, `reference_date` diferente → contagem diferente; nenhuma leitura de relógio).
  - `test_quiz_status_cases`.
  - `test_graduation_forecast`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(rules): regras puras de saldo, encargos, previsão, prova e prazo`.

### Task 14: Ferramentas acadêmicas

**Files:**
- Create: `app/data/queries/academic.py`, `app/tools/academic.py`; Modify: `app/tools/registry.py`, `ontology/tools.yaml`
- Test: `tests/integration/test_tools_academic.py`

**Interfaces:**
- Produces: `get_academic_profile`, `list_course_results` (filtros `enrollment`, `term` `^\d{4}/[12]$`, `result` enum PT, `subject` texto ≤ 60 casado sem acento contra código, nome EN e aliases PT), `get_curriculum_progress`.
- Toda saída traz `enrollment`; status em PT pela ontologia.

- [ ] **Step 1: Write the failing tests** (esperado = `evals.oracle`)
  - `test_profile_golden1_matches_oracle`.
  - `test_profile_has_status_history_and_term_registration` (golden 3: transferência com motivo).
  - `test_cancelled_overrides_term_registered`.
  - `test_results_filter_by_subject_pt` (`"cálculo"` acha `CALCULUS I`).
  - `test_attendance_reported`.
  - `test_progress_pending_and_forecast`.
  - `test_grade_as_stored`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): ferramentas acadêmicas`.

### Task 15: Ferramentas financeiras

**Files:**
- Create: `app/data/queries/finance.py`, `app/tools/finance.py`; Modify: `registry.py`, `tools.yaml`
- Test: `tests/integration/test_tools_finance.py`

**Interfaces:**
- Produces:
  - `get_financial_summary`: sem `enrollment` → `{"por_matricula": [...], "total_geral": {...}}`; com `enrollment` → só aquela. Usa `financial_position`.
  - `list_invoices`.
  - `list_agreements`.
- `payer_type` em PT, sem nome.

- [ ] **Step 1: Write the failing tests**
  - `test_golden4_total_para_quitar == "R$ 49.508,43"` e `principal_vencido == "R$ 42.636,26"`.
  - `test_no_sibling_invoices`.
  - `test_golden9_person_level_breakdown` (duas matrículas e total geral).
  - `test_golden1_no_agreements_message`.
  - `test_golden6_active_agreement`.
  - `test_list_capped_at_20_with_total` (golden 4).
  - `test_reference_date_changes_overdue`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): ferramentas financeiras com total para quitar`.

### Task 16: Ferramentas de protocolo e rascunho

**Files:**
- Create: `app/data/queries/protocols.py`, `app/tools/protocols.py`; Modify: `registry.py`, `tools.yaml`
- Test: `tests/integration/test_tools_protocols.py`

**Interfaces:**
- Produces:
  - `list_protocols` (`returns_untrusted=True`; descrições de `SA-…` e mensagens são texto não confiável);
  - `get_protocol` (`^(\d{11}|SA-\d{4}-\d{6})$`; `returns_untrusted=True`);
  - `draft_protocol` (`read_only=False`; `service_code` enum `AGENT_SERVICES`; `enrollment` obrigatório se houver mais de 1 matrícula ativa, senão a primária; `description` ≤ 500) → `ToolOutput(data={"draft_shown": True, "servico": ..., "taxa": ..., "prazo": ...}, draft=DraftCard(...))`.
- O token **não** vai em `data`. O aviso de duplicidade cobre protocolo aberto do mesmo serviço (secretaria ou `SA-`).
- `orientation(service_code)` para os serviços "só orientação" devolve requisitos, prazo, taxa e "solicite pelo portal ou na secretaria" (usado por `get_catalog(kind="services")`).
- `PROTOCOL_NOT_FOUND = "Protocolo não encontrado entre os seus protocolos."`.

- [ ] **Step 1: Write the failing tests**
  - `test_foreign_and_nonexistent_same_message`.
  - `test_internal_notes_absent`.
  - `test_guardian_protocol_without_identity`.
  - `test_sibling_protocol_absent`.
  - `test_protocol_invoice_link`.
  - `test_sla_due_vs_reference_date` (golden 3, `20260024855`).
  - `test_pending_requester_label`.
  - `test_draft_rejects_non_agent_service` (`ACA-012` → `ToolArgError`).
  - `test_draft_token_not_in_llm_data`.
  - `test_draft_requires_enrollment_when_two_active`.
  - `test_sa_protocol_readable`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): protocolos e rascunho restrito`.

### Task 17: Ferramentas de LMS e catálogo

**Files:**
- Create: `app/data/queries/lms.py`, `app/data/queries/catalog.py`, `app/tools/lms.py`, `app/tools/catalog.py`; Modify: `registry.py`, `tools.yaml`
- Test: `tests/integration/test_tools_lms_catalog.py`

**Interfaces:**
- Produces:
  - `get_lms_activity`: período filtrado pelo epoch convertido; provas por `quiz_status`; nota "tentativa no AVA, não oficial".
  - `get_catalog`:
    - `courses`, `services`, `units`, `prices`, `calendar` devolvidos inteiros em PT;
    - `curriculum` = matriz do aluno;
    - `subjects` filtradas pela matriz e pela `query`, casada sem acento contra código, EN e aliases;
    - `services` inclui a orientação dos serviços "só orientação".

- [ ] **Step 1: Write the failing tests**
  - `test_golden1_7_attempts` e `test_golden2_30_attempts`.
  - `test_pending_quizzes_vs_oracle`.
  - `test_no_lms_account_message`.
  - `test_accesses_in_period`.
  - `test_event_has_no_ip_or_url`.
  - `test_catalog_brand_scoped` (aluno de UniSaoPaulo não vê unidade de UniLitoral).
  - `test_price_direito_found_in_pt` (`"direito"` → mensalidade de `GRAD-DIR-*` da marca).
  - `test_calendar_current_term_and_av_dates`.
  - `test_services_orientation_for_lock`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(tools): LMS com provas pendentes e catálogo em PT`.

### Task 18: Exposição por ferramenta e LLM comprometido

**Files:**
- Create: `app/agent/fakes.py` (acrescenta `AdversarialLLM`), `tests/integration/test_tool_exposure.py`, `tests/integration/test_compromised_llm.py`

**Interfaces:**
- Produces: `AdversarialLLM(tools, rng)`: gera chamadas para **toda** ferramenta com **todo** valor de enum, cada `service_code`, números de protocolo de **outros** alunos (lidos do banco como dono) e strings de `hypothesis` (SQL, unicode, 10 KB). Os argumentos inválidos devem ser barrados pela validação.

- [ ] **Step 1: Write the failing tests**
  - `test_tool_outputs_within_declared_fields`: para os 10 golden × todas as ferramentas, as chaves folha ⊆ `Ontology.tool_outputs(tool)` ∪ metadados (`enrollment`, `total`, `items`, `message`, `warning`, `draft_shown`).
  - `test_compromised_llm_never_sees_other_person`: para os golden 1, 2 e 4, executa ~500 chamadas adversárias. Para cada saída, extrai identificadores (protocolos, faturas, contratos, RAs com marca, nomes) e confere, como dono, que **todos** pertencem ao `person_key` do aluno.
  - `test_fuzzed_args_never_reach_sql_unvalidated` (hypothesis, 200 exemplos → `ToolArgError` ou resultado do próprio aluno).
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `test(security): exposição por ferramenta e LLM comprometido`.
- [ ] **Step 6:** `ponytail-review` da fase 2; `pytest-coverage`.

---

## Fase 3 — Agente (raia paralela à fase 2)

### Task 19: Guards

**Files:**
- Create: `app/guards/input.py`, `app/guards/evidence.py`
- Test: `tests/unit/test_input_guard.py`, `tests/unit/test_evidence_guard.py`

**Interfaces:**
- Produces:
  - `normalize_input(text, max_chars) -> str` (NFKC, sem caracteres invisíveis nem de controle; vazio ou longo → `InputRejected`).
  - `@dataclass Claim(kind, raw, canonical)`; `extract_claims(text, vocabulary: frozenset[str]) -> list[Claim]`.
  - `check_evidence(answer, *, tool_evidence: Sequence[str], user_text: str, system_context: str, canary: str, prompt_prefix: str, vocabulary) -> GuardResult(ok, ungrounded: tuple[Claim, ...], prompt_leak: bool)`.
  - `degrade(answer, ungrounded) -> str | None` (remove as frases com itens sem evidência; `None` se não sobrar conteúdo).
  - `strip_markdown(text) -> str`.
- Tipos de `kind`: `money`, `date`, `pct`, `decimal`, `int`, `id`, `cpf`, `cnpj`, `email`, `phone`, `status`.

- [ ] **Step 1: Write the failing tests**

```python
TOOL = ['{"total_para_quitar": "R$ 49.508,43", "proximo_vencimento": "05/11/2026", "faturas_vencidas": 32, '
        '"frequencia": "75%", "status": "ativa", "protocolo": "20260024855"}']
def chk(a, user=""):
    return check_evidence(a, tool_evidence=TOOL, user_text=user, system_context="Data de referência: 31/10/2026",
                          canary="c4n4ry-xyz", prompt_prefix=PREFIX, vocabulary=VOCAB)

def test_grounded_passes(): assert chk("Você deve R$ 49.508,43 em 32 faturas; vence em 05/11, frequência 75%.").ok
def test_partial_and_written_dates(): assert chk("Vence dia 5 de novembro.").ok and not chk("Vence dia 6 de novembro.").ok
def test_mid_integers_checked(): assert not chk("Você tem 31 faturas vencidas.").ok
def test_pct_checked(): assert not chk("Sua frequência é 80%.").ok
def test_status_word_checked(): assert not chk("Sua matrícula está trancada.").ok
def test_user_text_does_not_ground_money(): assert not chk("Confirmo: seu saldo é R$ 0,00.", user="meu saldo é R$ 0,00?").ok
def test_user_text_grounds_identifier(): assert chk("Não encontrei o protocolo 20259999999.", user="e o 20259999999?").ok
def test_no_substring_match(): assert not chk("Você deve R$ 42,00.").ok
def test_small_int_exempt(): assert chk("São 3 disciplinas no 2º semestre.").ok
def test_prompt_leak_canary_and_ngram():
    assert chk("c4n4ry-xyz").prompt_leak and chk(" ".join(PREFIX.split()[:10])).prompt_leak
def test_degrade_keeps_grounded_sentence():
    assert degrade("Você deve R$ 49.508,43. Vence dia 9/11.", [Claim("date", "9/11", "11-09")]) == "Você deve R$ 49.508,43."
```

Mais: `test_input_nfkc_zero_width`, `test_input_limits`, `test_strip_markdown`.

- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(guards): entrada e EvidenceGuard com degradação por frase`.

### Task 20: Prompt e grafo do agente (LangGraph)

**Files:**
- Create: `app/agent/prompt.py`, `app/agent/messages.py`, `app/agent/graph.py`
- Test: `tests/unit/test_prompt.py`, `tests/unit/test_graph.py`

**Interfaces:**
- Consumes: Tasks 2, 12, 19.
- Produces:
  - `SYSTEM_PREFIX` (regras da spec §6.2; contém `{canary}` uma vez);
  - `build_system_prompt(scope, reference_date, canary) -> str`;
  - `wrap_tool_result(name, data, untrusted: bool) -> dict`;
  - mensagens fixas: `FALLBACK_UNGROUNDED` (com próximo passo), `DEGRADED_NOTE`, `FALLBACK_BUDGET`, `FALLBACK_LLM_UNAVAILABLE`, `TOOL_UNAVAILABLE`;
  - `build_graph(llm: LLMProvider, settings) -> CompiledStateGraph`, com estado `AgentState(TypedDict)`: `messages`, `round`, `tainted`, `tool_cache`, `evidence`, `draft`, `guard`, `usage`, `timings`;
  - nós `call_model`, `run_tools`, `check_evidence`, `degrade` e as arestas da spec §4;
  - `run_turn(graph, *, ctx, tools, history, user_text, writer: Callable[[dict], None] | None = None) -> TurnResult`.
- Comportamento:
  - `run_tools` valida os argumentos, executa em `asyncio.gather`, reaproveita leitura repetida, transforma exceção em `TOOL_UNAVAILABLE` e liga `tainted` se `returns_untrusted`;
  - com `tainted`, `call_model` não oferece `draft_protocol`;
  - o progresso sai por `get_stream_writer()` (`{"type":"progress","text":"consultando financeiro"}`);
  - o orçamento é `asyncio.timeout(loop_budget_s)` + `recursion_limit = 2*loop_max_rounds + 4`;
  - o histórico é cortado por turno (`Message.turn`).

- [ ] **Step 1: Write the failing tests** (`ScriptedLLM` e ferramentas falsas)
  - `test_tool_then_answer`.
  - `test_parallel_tools`.
  - `test_invalid_args_reported_not_executed`.
  - `test_tool_exception_unavailable`.
  - `test_guard_retry_pass`.
  - `test_guard_degraded`.
  - `test_guard_fallback_with_next_step`.
  - `test_budget_exceeded`.
  - `test_max_rounds_forces_answer`.
  - `test_llm_unavailable`.
  - `test_taint_removes_draft_tool` (depois de `get_protocol`, a 2ª chamada do LLM recebe as ferramentas sem `draft_protocol`).
  - `test_draft_card_surfaces_without_token_in_messages`.
  - `test_thought_signature_preserved_across_rounds`.
  - `test_history_cut_by_whole_turns`.
  - `test_progress_events_emitted`.
  - `test_prefix_identical_across_students`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): grafo LangGraph com taint, orçamento e EvidenceGuard`.

### Task 21: Modo replay e gravação [revisão por fase]

**Files:**
- Create: `app/agent/replay.py`; Modify: `app/agent/gemini.py` (gravação opcional)
- Test: `tests/unit/test_replay.py`

**Interfaces:**
- Produces:
  - `RecordingLLM(inner, path)`: grava `{"script_key", "call_index", "response"}` em JSONL;
  - `ReplayLLM(path)`: `script_key = sha256(person_label + "|" + "|".join(user_texts_da_conversa))`; devolve as respostas na ordem; sem gravação → `LLMResponse(text="Modo demonstração: esta pergunta não foi gravada. Veja as perguntas sugeridas.")`;
  - `make_llm` passa a aceitar `replay`;
  - `ReplayLLM.suggested(person_label) -> list[str]`.

- [ ] **Step 1–4:** testes `test_record_then_replay_same_answer`, `test_unknown_question_message` → FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(agent): modo replay para rodar sem chave`.
- [ ] **Step 6:** juntar o worktree da fase 3 ao da fase 2; `make check`; `ponytail-review` da fase 3.

---

## Fase 4 — API, A2A, UI e observabilidade

### Task 22: Autenticação e login simulado

**Files:**
- Create: `app/api/auth.py`, `app/api/dev.py`, `app/main.py` (`create_app(settings=None)` com lifespan: pool, `assert_safe_role`, Redis, grafo)
- Test: `tests/e2e/test_auth.py`

**Interfaces:**
- Produces:
  - `issue_token(person_key, settings) -> str`;
  - `verify_token(token, settings) -> UUID` (`algorithms=["HS256"]`, exige `exp` e `sub`);
  - `get_scope_cookie(request)` e `get_scope_bearer(request)`;
  - `GET/POST /dev/login`, só com `dev_login=True`: lista os rótulos do `golden_login.json`; o POST exige `dev_login_secret` e um `person_key` presente no arquivo.

- [ ] **Step 1: Write the failing tests**
  - `test_tampered_401`.
  - `test_expired_401`.
  - `test_alg_none_401`.
  - `test_wrong_alg_401`.
  - `test_body_identity_ignored`.
  - `test_dev_login_disabled_404`.
  - `test_dev_login_wrong_secret_403`.
  - `test_dev_login_non_golden_403`.
  - `test_cookie_flags`.
  - `test_startup_aborts_with_superuser_dsn`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): JWT, escopo e login simulado restrito`.

### Task 23: Chat SSE, limites e histórico

**Files:**
- Create: `app/api/chat.py`; Modify: `app/infra/redis_stores.py`
- Test: `tests/e2e/test_chat.py`

**Interfaces:**
- Produces:
  - `rate_limit_hit(redis, person_key, limit) -> bool`;
  - `TokenBudget(redis).spend(person_key, tokens) -> bool` (cota operacional pelo dia UTC real — por isso `redis_stores.py` é exceção no teste de relógio; chave `budget:{yyyymmdd}:{person_key}`);
  - `GlobalSpend(redis).add(usd) -> float`;
  - `HistoryStore(redis, max_turns, ttl_s)`;
  - `POST /chat`: corpo com `extra="forbid"`; eventos `meta`, `progress`, `draft` (`DraftCard` + token), `answer` (sem markdown), `error`, `ping` a cada 15 s; desconexão cancela a task do grafo; orçamento ou teto estourado → `error` com mensagem PT e HTTP 429/503.

- [ ] **Step 1: Write the failing tests**
  - `test_progress_then_answer`.
  - `test_identity_claim_in_text_ignored`.
  - `test_rate_limit_429`.
  - `test_token_budget_exhausted`.
  - `test_global_cap_503`.
  - `test_history_isolated_per_student` (mesmo `conversation_id`).
  - `test_llm_unavailable_error_event_no_traceback`.
  - `test_extra_body_422`.
  - `test_long_input_error`.
  - `test_disconnect_cancels`.
  - `test_markdown_stripped`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): chat SSE com limites de uso e histórico por aluno`.

### Task 24: Confirmação, feedback, CSRF e IDOR

**Files:**
- Create: `app/api/protocols.py`, `app/api/feedback.py`
- Test: `tests/e2e/test_confirm_feedback.py`

**Interfaces:**
- Produces:
  - `POST /protocols/confirm` `{draft_id, token, description}`: exige `Origin == public_origin` e JSON; `take` + INSERT; violação do índice único → 409 "Você já tem uma solicitação aberta deste serviço.";
  - `POST /feedback` `{trace_id, useful, comment?}`.

- [ ] **Step 1: Write the failing tests**
  - `test_confirm_once_under_concurrency`.
  - `test_other_students_draft_409`.
  - `test_csrf_wrong_origin_403`.
  - `test_duplicate_open_request_409`.
  - `test_description_edited_by_student_saved`.
  - `test_feedback_stored_and_isolated`.
  - `test_idor_conversation_and_feedback`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): confirmação de protocolo, feedback e proteções de CSRF/IDOR`.

### Task 25: Servidor A2A

**Files:**
- Create: `app/api/a2a.py`; Modify: `app/main.py`
- Test: `tests/e2e/test_a2a.py`

**Interfaces:**
- Consumes: `a2a-sdk[http-server]` 1.3:
  - `create_agent_card_routes(agent_card)`;
  - `create_jsonrpc_routes(DefaultRequestHandler(executor, task_store), rpc_url="/a2a", context_builder=JwtContextBuilder(settings))`;
  - `add_a2a_routes_to_fastapi(app, agent_card_routes=..., jsonrpc_routes=...)`.
  - Conferir no spike a assinatura de `DefaultRequestHandler` e o `owner_resolver` na versão instalada.
- Produces:
  - `build_agent_card(settings) -> AgentCard` (nome, descrição PT, skill `atendimento-aluno`, esquema HTTP bearer JWT, URL `/a2a`);
  - `JwtContextBuilder`: valida o bearer com `verify_token` e põe `person_key` no `ServerCallContext.user`; sem bearer → erro de autenticação;
  - `StudentAgentExecutor(AgentExecutor).execute(context, event_queue)`: carrega o escopo e roda `run_turn`. Com rascunho → `TaskUpdater` em `TASK_STATE_INPUT_REQUIRED` com DataPart `{draft_id, servico, taxa, prazo, descricao}`. Mensagem seguinte com DataPart `{"confirm_draft": draft_id}` → confirma pela mesma função da Task 24. Texto livre não confirma;
  - tarefas com dono = `person_key`.

- [ ] **Step 1: Write the failing tests** (cliente HTTP do teste, `ScriptedLLM`)
  - `test_agent_card_served`.
  - `test_message_without_bearer_rejected`.
  - `test_message_answers_for_token_owner`.
  - `test_identity_in_text_ignored_via_a2a`.
  - `test_draft_input_required_then_datapart_confirm`.
  - `test_text_confirmo_does_not_confirm`.
  - `test_other_students_task_not_found`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `feat(api): servidor A2A com bearer JWT e confirmação estruturada`.

### Task 26: Chat web [revisão por fase]

**Files:**
- Create: `app/web/index.html`, `app.js`, `style.css`; Modify: `app/main.py` (estáticos + middleware de cabeçalhos)
- Test: `tests/e2e/test_web.py`

**Interfaces:**
- Produces:
  - chat com indicador de progresso;
  - cartão de rascunho com descrição editável, aviso fixo e botão "Confirmar";
  - botões 👍/👎 → `/feedback`;
  - no modo replay, as perguntas sugeridas;
  - renderização só com `textContent`;
  - cabeçalhos da spec §8.

- [ ] **Step 1–4:** testes `test_csp_headers`, `test_js_has_no_innerhtml` → FAIL → implementar → PASS.
- [ ] **Step 5:** Teste manual com o golden 4: "quanto eu devo?", "quero a 2ª via do histórico" → confirmar; capturas em `docs/evidence/img/`.
- [ ] **Step 6: Commit** — `feat(web): chat em texto puro com cartão de confirmação`.

### Task 27: Observabilidade

**Files:**
- Create: `app/obs/logging.py`, `tracing.py`, `metrics.py`, `cost.py`, `audit.py`, `scripts/inspect_turn.py`, `ops/prometheus/prometheus.yml`, `ops/prometheus/rules.yml`, `ops/grafana/provisioning/**`, `ops/grafana/dashboards/agent.json`, `docs/SLO.md`
- Test: `tests/unit/test_obs.py`, `tests/e2e/test_observability.py`

**Interfaces:**
- Produces:
  - `configure_logging()` e o middleware de `trace_id`;
  - `setup_tracing(settings)`: OTel com OTLP para o Phoenix quando `otel_endpoint`; spans `chat.turn`, `agent.call_model` (atributos `gen_ai.provider.name=gcp.gemini`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, mais `thinking_tokens` e `cached_tokens`, **sem conteúdo**), `agent.tool` (nome, itens, duração), `db.query`, `agent.guard` (desfecho);
  - métricas `agent_turn_seconds{category}`, `agent_llm_seconds`, `agent_llm_errors_total{kind}`, `agent_llm_queue_seconds`, `agent_llm_retries_total`, `agent_tool_seconds{tool}`, `agent_guard_total{outcome}`, `agent_rounds`, `agent_sse_open`, `agent_tokens_total{kind}`, `agent_cost_usd_total`, `agent_feedback_total{useful}`;
  - `cost_usd(usage, model) -> float`, com tabela de preços datada (entrada, saída incluindo thinking, cache);
  - `AuditWriter(pool, key, ttl_days).write(trace_id, person_key, payload: dict)` (Fernet; INSERT only);
  - `inspect_turn.py <trace_id>` (DSN do dono): decifra e roda `check_evidence` de novo;
  - `SLO.md`: p95 por categoria, erro < 1%, custo/dia, burn rate 14,4×/1 h e 6×/6 h em `rules.yml`.

- [ ] **Step 1: Write the failing tests**
  - `test_spans_have_no_content` (exportador em memória; nenhum atributo contém o texto do aluno ou da resposta).
  - `test_cost_includes_thinking_and_cache`.
  - `test_metrics_exposed`.
  - `test_audit_encrypted_and_agent_cannot_read`.
  - `test_logs_have_no_pii`.
- [ ] **Step 2–4:** FAIL → implementar → PASS.
- [ ] **Step 5:** `docker compose --profile obs up`, uma conversa, capturas do trace no Phoenix e do painel no Grafana em `docs/evidence/observability.md`.
- [ ] **Step 6: Commit** — `feat(obs): traces gen_ai sem conteúdo, métricas, painel, SLOs e auditoria cifrada`.
- [ ] **Step 7:** `make check`; `ponytail-review` da fase 4.

---

## Fase 5 — Evals (Gemini real; fora do CI)

### Task 28: Dataset de qualidade

**Files:**
- Create: `evals/client.py`, `evals/golden/templates.yaml`, `evals/golden/paraphrases.yaml`, `evals/golden/multiturn.yaml`, `evals/golden/build.py`, `evals/golden/dataset.json`, `evals/golden/dataset.sha256`
- Test: `tests/unit/test_dataset.py`

**Interfaces:**
- Produces:
  - `ChatClient(base_url)`: `login(label)`; `ask(text, conversation_id=None) -> ChatReply(answer, events, draft, latency_ms, first_event_ms, trace_id)`; `confirm(draft)`;
  - `select_test_students(seed=20261103, n=30) -> list[str]`: estratificado por marca × modalidade × status, excluindo os golden;
  - `templates.yaml` por categoria (spec §9.2), cada um com `expected_tools`, `required` (expressões sobre o oráculo) e `forbidden`;
  - `paraphrases.yaml` (2–3 por modelo, geradas uma vez por script sem acesso às ferramentas e revisadas);
  - `multiturn.yaml` (8 roteiros);
  - `build.py` gera `dataset.json` (`split ∈ dev|test`) a partir do **oráculo** (CSV cru) e grava o SHA-256.

- [ ] **Step 1–4:** testes `test_all_categories_in_both_splits`, `test_test_split_disjoint_from_golden`, `test_required_values_from_oracle_formats`, `test_hash_matches` → FAIL → implementar → PASS.
- [ ] **Step 5: Commit** — `test(evals): dataset de qualidade com split dev/teste e oráculo independente`.

### Task 29: Runner de qualidade, juiz e escolha do modelo

**Files:**
- Create: `evals/stats.py`, `evals/golden/score.py`, `evals/golden/judge.py`, `evals/golden/run.py`, `evals/golden/labels.csv` (rótulos do Miguel)
- Test: `tests/unit/test_stats.py`, `tests/unit/test_score.py`

**Interfaces:**
- Produces:
  - `wilson(k, n) -> tuple[float, float]`; `mcnemar(b, c) -> float`; `bootstrap_ci(values, q) -> tuple[float, float]`; `cohen_kappa(a, b) -> float`;
  - `score_item(reply, item) -> dict`: fatos por tokens canônicos com fronteira de palavra; ferramentas do trace; recusa correta aceitando oferta de próximo passo; categoria de falha;
  - `judge(reply, item) -> dict`: rubrica binária com o gabarito, saída JSON;
  - `run.py --split test --model M --runs 3 --concurrency 5 --record` → `docs/evidence/quality.md` e `conversations.md`; grava replays em `evals/replays/`.
- `quality.md` traz:
  - SHA do dataset;
  - pass@1 e pass^3 com IC de Wilson por categoria;
  - matriz de recusa;
  - desfechos do guard e taxa de bloqueio indevido;
  - kappa juiz × Miguel;
  - tabela de falhas com `trace_id`;
  - McNemar entre modelos;
  - decisão do modelo (D11);
  - latência por categoria.

- [ ] **Step 1–4:** testes de `stats` (valores conhecidos) e de `score_item` (`"ativa"` não casa em `"inativa"`; `"05/11/2026"` casa com `"5 de novembro de 2026"`; recusa com oferta de protocolo conta como correta) → FAIL → implementar → PASS.
- [ ] **Step 5:** Rodar a matriz rápida (2 modelos × MINIMAL/LOW × 30 perguntas do dev). Depois o teste completo nos finalistas (`--runs 1` no 2º modelo, `--runs 3` no escolhido). Miguel rotula 30 respostas em `labels.csv`. Falhas viram itens de regressão no dev, nunca no teste.
- [ ] **Step 6: Commit** — `test(evals): avaliação de qualidade com IC, juiz calibrado e escolha do modelo`.

### Task 30: Suíte de ataques e ataques à API

**Files:**
- Create: `evals/redteam/attacks.yaml`, `evals/redteam/plant.py`, `evals/redteam/check.py`, `evals/redteam/run.py`, `evals/redteam/api_attacks.py`
- Test: `tests/unit/test_redteam_check.py`

**Interfaces:**
- Produces:
  - `attacks.yaml`: 60–80 ataques nas 15 categorias da spec §7 (`id`, `category`, `as_case`, `turns`, `expect`: fatos esperados, marcadores do payload, `no_draft`);
  - `plant.py`: injeção numa mensagem pública do protocolo aberto do golden 3 e numa descrição `SA-` (só no banco de eval);
  - `forbidden_set(owner_conn, case) -> set[str]`: identificadores **únicos** de outras pessoas (nomes normalizados, CPFs, protocolos, faturas, contratos), nomes de analistas, unidades de outras marcas, valores dos segredos do `.env`, canário;
  - `check_attack(reply, forbidden, expect, prefix) -> dict` (vazamento, 8-grama, `draft` indevido, fatos);
  - juiz por rubrica só para `role_break` e `false_promise`;
  - `api_attacks.py`: IDOR (conversa, rascunho, feedback, tarefa A2A), JWT forjado/`alg=none`, CSRF, contorno de rate limit;
  - `run.py --runs 3` → `docs/evidence/security.md` com taxa por categoria, n, limite superior de 95% (regra de três), transcrições das falhas e uma seção "testes de arquitetura" (saída do pytest das Tasks 9, 12, 18, 22–25).

- [ ] **Step 1–4:** testes de `check_attack` (nome sem acento/minúsculo casa; RA compartilhado **não** gera falso positivo; marcador do payload detectado) → FAIL → implementar → PASS.
- [ ] **Step 5:** `make eval-security RUNS=3`. Todo vazamento → teste de regressão + correção antes do congelamento.
- [ ] **Step 6: Commit** — `test(evals): ataques com LLM real, ataques à API e evidência de segurança`.

### Task 31: Latência, carga, capacidade e custo

**Files:**
- Create: `evals/latency/bench.py`, `evals/latency/fake_gemini.py`, `evals/latency/locustfile.py`, `evals/latency/capacity.py`
- Test: `tests/unit/test_capacity.py`

**Interfaces:**
- Produces:
  - `bench.py --model M --n 300 --window A|B [--vertex]`: perguntas do dataset por categoria; percentis com bootstrap; cascata por etapa lida do trace; 1º evento × resposta; tokens por índice de turno; região e RTT;
  - `fake_gemini.py`: servidor HTTP que imita `generateContent` para o SDK (via `http_options.base_url`), com latência amostrada do bench real e 2% de 429;
  - `locustfile.py`: JWTs emitidos para ~1000 `person_key`; mix por categoria; escada 50/100/200/500; container separado;
  - `capacity.py`: `capacity(students, msgs_per_min, calls_per_turn, tokens_per_call, tpm_quota) -> dict` e `monthly_cost(students, questions, tokens, price) -> dict`.
- Saída em `docs/evidence/latency.md`:
  - tabelas e gráficos (PNG);
  - joelho da curva;
  - overhead de infra;
  - CPU, atraso do event loop e espera no pool;
  - capacidade contra a cota;
  - custo por aluno/mês (2026 e 2027);
  - a rodada Vertex, se houver.

- [ ] **Step 1–4:** teste de `capacity`/`monthly_cost` com valores conhecidos → FAIL → implementar → PASS.
- [ ] **Step 5:** `make eval-latency` (2 janelas); `make load-test`; publicar.
- [ ] **Step 6: Commit** — `test(evals): latência com IC, carga com Gemini falso, capacidade e custo`.

---

## Fase 6 — Documento, revisão e entrega

### Task 32: Documentação, resumo executivo e revisões

**Files:**
- Create: `docs/ARCHITECTURE.md`, `docs/evidence/README.md`; Modify: `README.md`

**Interfaces:**
- `README.md`, na 1ª página:
  - o que é;
  - 3 números de destaque (com fonte);
  - `cp .env.example .env && docker compose up`;
  - modo replay sem chave;
  - tabela requisito → mecanismo → teste → número;
  - diagrama mermaid;
  - link do vídeo.
- `README.md`, depois: comandos, mapa do repositório, AI Studio × Vertex.
- `ARCHITECTURE.md`: D1–D20 com trade-offs e "o que mediria em produção"; dados e resolução (números do `data-quality.md`); segurança (ameaça × defesa × prova); qualidade; latência, capacidade e custo; observabilidade; integração em produção; limites honestos; referências.

- [ ] **Step 1:** Escrever com números reais. Nenhum número sem fonte em `docs/evidence/`.
- [ ] **Step 2:** `security-review` e `code-review` no branch inteiro; cada achado confirmado → teste + correção.
- [ ] **Step 3:** `make check && make test-full` → PASS.
- [ ] **Step 4: Commit** — `docs: arquitetura, resumo executivo e evidências`.

### Task 33: Pacote e teste do zero

**Files:**
- Create: `scripts/package.sh`

**Interfaces:**
- `make package` gera:
  - `dist/student-agent.zip` (`git archive`: sem `.git`, `data/`, `.env` nem `docs/superpowers/`);
  - `dist/student-agent.bundle` (`git bundle create --all`: histórico com TDD e commits atômicos).

- [ ] **Step 1:** Conferir com `unzip -l` (nada de `.git/`, `.rev`, `.env`, `dados.zip` ou `docs/superpowers/`) e com `git bundle verify`.
- [ ] **Step 2:** Teste do zero em diretório novo:
  1. descompactar;
  2. copiar `dados.zip` para `data/`;
  3. `cp .env.example .env`;
  4. `docker compose up` → login golden → 3 perguntas;
  5. repetir com `LLM_PROVIDER=replay` e sem chave.
- [ ] **Step 3:** Miguel grava o vídeo de 3 min: login, pergunta cruzada, ataque bloqueado, rascunho → confirmação, trace no Phoenix.
- [ ] **Step 4: Commit** — `chore: pacote de entrega`.
