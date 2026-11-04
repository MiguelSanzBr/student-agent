.PHONY: stamp up down lane-up lane-down smoke login a2a-demo investigate phoenix-import test test-full \
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

a2a-demo investigate phoenix-import eval-dev eval-test eval-guard eval-security \
eval-latency load-test package:
	$(NOT_YET)

smoke:  # make smoke LANE=x, em série; sobe a raia e a derruba ao fim
	@test -n "$(call lv,API_PORT)" || { echo "use LANE=a|b|c|d" >&2; exit 1; }
	$(LANE_ENV) DC='docker compose -f docker-compose.yml -f ops/compose.lane.yml' \
		PORT=$(call lv,API_PORT) sh scripts/smoke.sh

login:
	@test -n "$(CASE)" || { echo "use CASE=4" >&2; exit 1; }
	@docker compose exec -T -e PUBLIC_BASE_URL=http://localhost:$${API_PORT:-8000} api \
		python -m scripts.login_url $(CASE)

stamp:
	uv run ots stamp evals/golden/dataset.sha256
