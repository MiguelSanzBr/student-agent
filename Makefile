.PHONY: stamp up down lane-up lane-down smoke login a2a-demo phoenix-import test test-full \
        test-live lint check eval-dev eval-test eval-guard eval-security eval-latency load-test package

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
NOT_YET = @echo "ainda não implementado: $@" >&2; exit 1

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

test-live:
	uv run pytest -m live -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

check: lint test

# Runner da qualidade: no host (precisa de EVAL_API_URL, ETL_OUT_DIR, EVAL_OUT_DIR e dos segredos do
# app para `evals.tokens.mint`). make eval-dev MODEL=gemini-3.8-flash ROUND=1 [ARGS="--paraphrase"]
eval-dev eval-test:
	uv run python -m evals.golden.run --split $(patsubst eval-%,%,$@) --model $(MODEL) \
	  --round $(ROUND) --record $(ARGS)

eval-guard:  # make eval-guard SEED=<semente do cego> [ARGS="--dev-transcripts .../transcripts.jsonl"]
	uv run python -m evals.guard.run --blind-seed $(SEED) $(ARGS)

package:
	$(NOT_YET)

eval-latency:  # make eval-latency WINDOW=A|B|C [N=100] [MODEL=gemini-3.8-flash]
	@test -n "$(WINDOW)" || { echo "use WINDOW=A|B|C" >&2; exit 1; }
	uv run python -m evals.latency.bench --window $(WINDOW) --per-class $(or $(N),100) $(if $(MODEL),--model $(MODEL))

load-test:  # escadas (a) e (b) do loadgen; rodar sozinho na máquina (docs/evidence/latency.md)
	uv run python -m evals.latency.load_test

EVAL_DC = docker compose -f docker-compose.yml -f ops/compose.eval.yml --profile eval
RT = $(EVAL_DC) run --rm redteam python -m evals.redteam
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

login:
	@test -n "$(CASE)" || { echo "use CASE=4" >&2; exit 1; }
	@docker compose exec -T -e PUBLIC_BASE_URL=http://localhost:$${API_PORT:-8000} api \
		python -m scripts.login_url $(CASE)

stamp:
	uv run ots stamp evals/golden/dataset.sha256
