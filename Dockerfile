FROM python:3.12-slim
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
COPY tests/fixtures/golden.json ./tests/fixtures/golden.json
COPY scripts ./scripts
COPY slo_targets.yaml ./
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001
