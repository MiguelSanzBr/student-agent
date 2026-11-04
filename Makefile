.PHONY: stamp up down lane-up lane-down smoke login a2a-demo langflow-demo langflow-down phoenix-import test test-full \
        test-live lint check eval-dev eval-test eval-guard eval-security eval-security-blind eval-latency refit-window-a load-test publish-evidence package

LANE ?=
lane = $(shell echo $(LANE) | tr a-z A-Z)
lv = $(shell sed -n 's/^$(lane)_$(1)=//p' ops/lanes.env)
LANE_ENV = COMPOSE_PROJECT_NAME=$(call lv,COMPOSE_PROJECT_NAME) API_PORT=$(call lv,API_PORT) \
           DB_PORT=$(call lv,DB_PORT) REDIS_PORT=$(call lv,REDIS_PORT)
LANE_DC = $(LANE_ENV) docker compose -f docker-compose.yml -f ops/compose.lane.yml
# Testes de integração rodam no host e precisam das senhas da raia: `make lane-up` copia-as para
# .lanes/<raia>/ (dono = usuário do host, modo 0400, gitignored). Os testes leem LANE_SECRETS_DIR
# (ex.: LANE_SECRETS_DIR=.lanes/d); arquivos: postgres_password, etl_owner_password,
# agent_app_password, ops_audit_password, redis_password. Sobrescritos a cada lane-up.
LANE_DIR = $(CURDIR)/.lanes/$(shell echo $(LANE) | tr A-Z a-z)
LANE_COPY = for f in db/postgres_password db/etl_owner_password db/agent_app_password \
            db/ops_audit_password redis/redis_password; do \
            n=$${f\#*/}; cp /out/$$f /lane/$$n; \
            chown $(shell id -u):$(shell id -g) /lane/$$n; chmod 400 /lane/$$n; done

up:
	docker compose up -d --build --wait

down:
	docker compose down

lane-up:
	@test -n "$(call lv,API_PORT)" || { echo "use LANE=a|b|c|d" >&2; exit 1; }
	$(LANE_DC) up -d --build --wait init-secrets db redis
	@mkdir -p $(LANE_DIR)
	@$(LANE_DC) run --rm --no-deps -v $(LANE_DIR):/lane init-secrets sh -c '$(LANE_COPY)'
	@echo "segredos da raia: LANE_SECRETS_DIR=$(LANE_DIR)"

lane-down:
	@test -n "$(call lv,API_PORT)" || { echo "use LANE=a|b|c|d" >&2; exit 1; }
	$(LANE_DC) down -v

test:
	uv run pytest tests/unit -q

test-full:
	uv run pytest -q

# Exporte as chaves antes (sem imprimir): set -a; . ./.env; set +a; make test-live
# Modelos: ANTHROPIC_MODEL / OPENAI_MODEL (padrão de cada provedor), nunca o LLM_MODEL.
test-live:
	uv run pytest -m live -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

check: lint test

# Runner da qualidade (Task 31): no container `evals` (segredos em /run/secrets, dados e ETL por
# volume; só dados.zip é montado). Antes: o ETL precisa ter rodado (eval_students.json,
# golden_login.json). O container não tem git: GIT_SHA/GIT_DIRTY/FROZEN_SHA vêm daqui.
#   make eval-dev  MODEL=claude-haiku-5-5 ROUND=1 [ARGS=...]    (dev: ARGS=--rehearsal = ensaio)
#   (MODEL=gemini-3.8-flash ou gpt-6.1-terra também; MODEL vira o LLM_MODEL da api-eval)
#   make eval-test MODEL=claude-haiku-5-5 ROUND=1 HOLDOUT=/caminho/templates_test_only.yaml \
#                  ARGS="--dev-json /eval-out/quality/dev-r2/quality.json --spend-today X --rpd-left N"
# HOLDOUT (opt-in, nunca padrão): o YAML decifrado pelo autor, montado só nesta execução.
# O comando que apaga volumes do compose (down -v) apaga o eval-out (transcripts, marcador do
# cego): o ledger docs/evidence/quality/rounds.jsonl (git) guarda as rodadas e a medição cega,
# então um reset aparece.
GIT_ENV = GIT_SHA=$$(git rev-parse HEAD) GIT_DIRTY=$$(test -n "$$(git status --porcelain)" && echo 1 || echo 0) \
          FROZEN_SHA=$$(git rev-list -n 1 eval-frozen 2>/dev/null || true)
# Rodada dev deixa evidência não rastreada em docs/evidence/quality: commitar antes do eval-test
# (o teste exige árvore limpa, SHA congelado). DATA_SRC aponta o zip.
EVALS = $(GIT_ENV) $(EVAL_DC) run --rm $(ND) $(if $(HOLDOUT),-v $(abspath $(HOLDOUT)):/holdout/templates.yaml:ro) \
        evals python -m
eval-dev eval-test:
	@test -n "$(MODEL)" -a -n "$(ROUND)" || { echo "use MODEL=... ROUND=N" >&2; exit 1; }
	@test -z "$(HOLDOUT)" -o -f "$(HOLDOUT)" || { echo "HOLDOUT=$(HOLDOUT): arquivo inexistente" >&2; exit 1; }
	mkdir -p docs/evidence/quality && chmod a+rwx docs/evidence/quality
	$(GIT_ENV) LLM_MODEL=$(MODEL) $(EVAL_DC) up -d --build --wait api-eval
	$(EVAL_DC) build evals
	$(EVALS) evals.golden.run --split $(patsubst eval-%,%,$@) --model $(MODEL) --round $(ROUND) \
	  --record $(if $(HOLDOUT),--holdout-yaml /holdout/templates.yaml) $(ARGS)

eval-guard: ND = --no-deps  # guard só precisa do zip, EVAL_OUT_DIR e da evidência: sem segredos/DB/API
eval-guard:  # cego medido 1x (semente pré-registrada); ARGS="--dev-transcripts /eval-out/quality/dev-rN/transcripts.jsonl"
	@test -z "$(HOLDOUT)" -o -f "$(HOLDOUT)" || { echo "HOLDOUT=$(HOLDOUT): arquivo inexistente" >&2; exit 1; }
	mkdir -p docs/evidence/quality && chmod a+rwx docs/evidence/quality
	$(EVAL_DC) build evals
	$(EVALS) evals.guard.run $(ARGS)

package:  # MODE=a|b (ver scripts/package.sh)
	@bash scripts/package.sh

# Latência (Task 33): o runner roda em container (segredos do app para o JWT, ETL_OUT_DIR, dados em
# /data); a API alvo é a de perfil eval (api-eval; API_URL=... troca) iniciada com LLM_MODEL=$(MODEL).
LAT = $(EVAL_DC) run --build --rm -e EVAL_OUT_DIR=/out redteam python -m evals.latency
eval-latency:  # make eval-latency WINDOW=A|B|C [N=100] MODEL=claude-haiku-5-5 [ARGS=...]
	@test -n "$(WINDOW)" || { echo "use WINDOW=A|B|C" >&2; exit 1; }
	$(LAT).bench --window $(WINDOW) --per-class $(or $(N),100) --data /data/dados.zip \
	  --base-url $(or $(API_URL),http://api-eval:8000) --model $(or $(MODEL),claude-haiku-5-5) $(ARGS)

# Reajuste único da janela A (no host): depois do eval-latency WINDOW=A, `make publish-evidence`
# copia o latency-A-samples.jsonl do volume eval-out para docs/evidence/; então
# make refit-window-a SAMPLES=docs/evidence/latency-A-samples.jsonl (edita só 4 seções do slo_targets.yaml).
refit-window-a:
	@test -n "$(SAMPLES)" || { echo "use SAMPLES=<latency-A-samples.jsonl>" >&2; exit 1; }
	uv run python -m evals.latency.refit --samples $(SAMPLES)

# Escadas (a) e (b) contra a API (LLM_PROVIDER=loadtest, Redis e limitador reais), sem gastar:
# make load-test LANE=d [LADDER=a|b] [WORKERS=1|2] [CPUS=1.0] [ARGS=--duration 30]; roda sozinho na
# máquina; a raia fica de pé (make lane-down LANE=x). Precisa do ETL carregado na raia (`etl` com DATA_SRC=...), que grava o golden_login.json.
LOAD_MODEL ?= claude-haiku-5-5  # API (compose.load.yml) e runner usam o mesmo
LDC = $(LANE_ENV) LOAD_MODEL=$(LOAD_MODEL) UVICORN_WORKERS=$(or $(WORKERS),1) API_CPUS=$(or $(CPUS),1.0) \
      SLO_FILE=$(LANE_DIR)-slo.yaml docker compose -f docker-compose.yml -f ops/compose.lane.yml -f ops/compose.eval.yml -f ops/compose.load.yml --profile eval
load-test:
	@test -n "$(call lv,API_PORT)" || { echo "use LANE=a|b|c|d" >&2; exit 1; }
	uv run python -m evals.latency.load_test --ladder $(or $(LADDER),a) --model $(LOAD_MODEL) --write-slo $(LANE_DIR)-slo.yaml
	$(LDC) up -d --build --wait api
	@$(LDC) run --build --rm --no-deps -e EVAL_OUT_DIR=/out redteam python -m evals.latency.load_test \
	  --base-url http://api:8000 --model $(LOAD_MODEL) --ladder $(or $(LADDER),a) --workers $(or $(WORKERS),1) $(ARGS); \
	  rc=$$?; $(LDC) rm -sf api; exit $$rc

EVAL_DC = docker compose -f docker-compose.yml -f ops/compose.eval.yml --profile eval
RT = $(EVAL_DC) run --rm --build redteam python -m evals.redteam
# Ataques (Task 32): ETL do banco real, banco plantado em api-redteam, ataques diretos à API (alunos
# 3 e 4) e a suíte de desenvolvimento (alunos 1 e 2, 2 repetições). Exige SPEND_TODAY e RPD_LEFT
# (US$ já gastos hoje e requisições restantes do modelo; preflight). RT_ARGS=--label rerun nas
# rodadas depois das correções. Falha em qualquer etapa faz o alvo falhar, mas as duas rodam.
eval-security:
	@test -n "$(SPEND_TODAY)" -a -n "$(RPD_LEFT)" || { echo "use SPEND_TODAY=0.00 RPD_LEFT=5000 (gasto hoje em USD, requisições restantes)" >&2; exit 1; }
	$(EVAL_DC) up -d --build --wait init-secrets db redis
	$(EVAL_DC) --profile etl run --rm etl
	$(EVAL_DC) up -d --build --wait api-eval api-redteam
	@rc=0; $(RT).api_attacks --base-url http://api-eval:8000 --recall-control || rc=1; \
	$(RT).run --suite dev --reps 2 --base-url http://api-eval:8000 \
	  --planted-url http://api-redteam:8000 --spend-today $(SPEND_TODAY) --rpd-left $(RPD_LEFT) \
	  $(RT_ARGS) || rc=1; exit $$rc

# Cego de ataques (medição única; árvore limpa e SHA == eval-frozen): o YAML decifrado pelo autor é
# montado só nesta execução (read-only); o resultado entra em security.json["holdout"].
eval-security-blind:  # make eval-security-blind HOLDOUT=/caminho/attacks_blind.yaml SPEND_TODAY=0.00 RPD_LEFT=5000
	@test -n "$(HOLDOUT)" -a -f "$(HOLDOUT)" || { echo "use HOLDOUT=<yaml decifrado> (arquivo inexistente: $(HOLDOUT))" >&2; exit 1; }
	@test -n "$(SPEND_TODAY)" -a -n "$(RPD_LEFT)" || { echo "use SPEND_TODAY=0.00 RPD_LEFT=5000" >&2; exit 1; }
	mkdir -p docs/evidence/quality && chmod a+rwx docs/evidence/quality
	$(EVAL_DC) up -d --build --wait init-secrets db redis
	$(EVAL_DC) --profile etl run --rm etl
	$(EVAL_DC) up -d --build --wait api-eval
	$(GIT_ENV) $(EVAL_DC) run --rm --build -v $(abspath $(HOLDOUT)):/holdout/attacks.yaml:ro \
	  -v $(abspath docs/evidence/quality):/evidence -e EVIDENCE_DIR=/evidence -e GIT_SHA -e GIT_DIRTY -e FROZEN_SHA \
	  redteam python -m evals.redteam.run --suite blind --templates /holdout/attacks.yaml --reps 2 \
	  --base-url http://api-eval:8000 --spend-today $(SPEND_TODAY) --rpd-left $(RPD_LEFT) $(RT_ARGS)

# Copia só os públicos do volume eval-out (read-only) para docs/evidence (lista fechada em
# scripts/publish_evidence.py); depois: python scripts/render_docs.py.
publish-evidence:
	mkdir -p docs/evidence && chmod a+rwx docs/evidence
	$(EVAL_DC) run --rm --no-deps -v eval-out:/out:ro -v $(abspath docs/evidence):/evidence -v $(abspath scripts/publish_evidence.py):/publish_evidence.py:ro \
	  --entrypoint python redteam /publish_evidence.py /out /evidence

phoenix-import:  # make phoenix-import FILE=docs/evidence/phoenix/<run>.json (PHOENIX_URL)
	@test -n "$(FILE)" || { echo "use FILE=...json" >&2; exit 1; }
	uv run python -m scripts.phoenix_export import $(FILE)

investigate:  # make investigate TRACE=<id> [LANE=x]; lê turn_output como ops_audit
	@test -n "$(TRACE)" || { echo "use TRACE=<id>" >&2; exit 1; }
	uv run python -m scripts.investigate --trace $(TRACE)

smoke:  # make smoke LANE=x, em série; sobe a raia e a derruba ao fim
	@test -n "$(call lv,API_PORT)" || { echo "use LANE=a|b|c|d" >&2; exit 1; }
	$(LANE_ENV) DC='docker compose -f docker-compose.yml -f ops/compose.lane.yml' \
		PORT=$(call lv,API_PORT) sh scripts/smoke.sh

a2a-demo:  # com a pilha no ar (make up); CASE=4 por padrão
	@docker compose exec -T api python -m scripts.a2a_demo $(or $(CASE),4)

# Sobe api + Langflow num projeto próprio (portas fora das raias), roda o golden 4 pelo fluxo
# Chat Input -> A2A Agent -> Chat Output e derruba o que subiu. Sem chave de LLM (replay).
LF_PORT ?= 7860
LF_DC = COMPOSE_PROJECT_NAME=sa-langflow API_PORT=8014 DB_PORT=55436 REDIS_PORT=56383 \
        LANGFLOW_PORT=$(LF_PORT) LLM_PROVIDER=auto DEV_LOGIN=true A2A_PUBLIC_URL=http://api:8000 \
        docker compose -f docker-compose.yml -f ops/compose.lane.yml --profile langflow
langflow-demo:  # KEEP=1 deixa no ar para a captura de tela (derrube com make langflow-down)
	@trap '[ -n "$(KEEP)" ] || $(LF_DC) down --volumes >/dev/null 2>&1' EXIT; set -e; \
	$(LF_DC) up -d --build --wait init-secrets db redis; \
	$(LF_DC) run --rm etl; \
	$(LF_DC) up -d --build --wait api; \
	$(LF_DC) up -d langflow; \
	for i in $$(seq 60); do curl -fs http://127.0.0.1:$(LF_PORT)/health >/dev/null && break; sleep 5; done; \
	$(LF_DC) exec -T api python -m scripts.langflow_demo < ops/langflow/flow.json > docs/evidence/langflow-a2a.json; \
	cat docs/evidence/langflow-a2a.json

langflow-down:
	@$(LF_DC) down --volumes

login:
	@test -n "$(CASE)" || { echo "use CASE=4" >&2; exit 1; }
	@docker compose exec -T -e PUBLIC_BASE_URL=http://localhost:$${API_PORT:-8000} api \
		python -m scripts.login_url $(CASE)

stamp:
	uv run ots stamp evals/golden/dataset.sha256
