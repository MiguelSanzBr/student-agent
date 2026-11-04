FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY core ./core
COPY app ./app
COPY etl ./etl
COPY ontology ./ontology
COPY evals/__init__.py evals/oracle.py ./evals/
COPY evals/replays ./evals/replays
COPY scripts ./scripts
COPY slo_targets.yaml ./
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app

# Imagem dos runners de avaliação (compose.eval: redteam, etl-redteam). A da API (último estágio,
# o padrão) não leva templates de ataque, golden, tokens nem cliente de avaliação.
# o golden.json (CPFs de teste) só entra nos estágios que o leem: etl (golden_login) e eval; nunca na API
FROM base AS etl
COPY tests/fixtures/golden.json ./tests/fixtures/golden.json
USER 10001

FROM base AS eval
COPY tests/fixtures/golden.json ./tests/fixtures/golden.json
COPY evals/client.py evals/stats.py evals/preflight.py evals/tokens.py ./evals/
COPY evals/golden ./evals/golden
COPY evals/redteam ./evals/redteam
COPY evals/guard ./evals/guard
# só o verificador do holdout e o manifesto (SHA-256); nunca o .age, o seal.py nem a chave
COPY evals/holdout/__init__.py evals/holdout/verify.py evals/holdout/manifest.json ./evals/holdout/
# o guard importa perturb_dev, que usa estes fixtures de teste
COPY tests/__init__.py ./tests/__init__.py
COPY tests/fixtures/__init__.py tests/fixtures/entity_records.py ./tests/fixtures/
COPY evals/latency ./evals/latency
USER 10001

FROM base AS api
USER 10001
