.PHONY: up down lane-up lane-down smoke login a2a-demo investigate phoenix-import test test-full \
        test-live lint check eval-dev eval-test eval-guard eval-security eval-latency load-test package

LANE ?=
lane = $(shell echo $(LANE) | tr a-z A-Z)
lv = $(shell sed -n 's/^$(lane)_$(1)=//p' ops/lanes.env)
LANE_ENV = COMPOSE_PROJECT_NAME=$(call lv,COMPOSE_PROJECT_NAME) API_PORT=$(call lv,API_PORT) \
           DB_PORT=$(call lv,DB_PORT) REDIS_PORT=$(call lv,REDIS_PORT)
LANE_DC = $(LANE_ENV) docker compose -f docker-compose.yml -f ops/compose.lane.yml
NOT_YET = @echo "ainda não implementado: $@" >&2; exit 1

up:
	docker compose up -d --build --wait

down:
	docker compose down

lane-up:
	@test -n "$(call lv,API_PORT)" || { echo "use LANE=a|b|c|d" >&2; exit 1; }
	$(LANE_DC) up -d --build --wait init-secrets db redis

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

smoke login a2a-demo investigate phoenix-import eval-dev eval-test eval-guard eval-security \
eval-latency load-test package:
	$(NOT_YET)
