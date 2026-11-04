# student-agent

Agente de atendimento ao aluno (case técnico). Spec e plano em `docs/superpowers/` (pasta que não vai no pacote de entrega (b); ver `scripts/package.sh`).

## Comandos
- `uv sync` · `make test` (unit) · `make test-full` · `make lint` (ruff + mypy --strict) · `make check`
- `make up` / `make down` (stack completa); `make lane-up LANE=a|b|c|d` / `make lane-down LANE=...`
  sobem só `init-secrets db redis` com Postgres efêmero e portas próprias (`ops/lanes.env`).
- Todos os alvos do Makefile estão implementados: `smoke`, `eval-dev|test|guard|security|latency`, `load-test`, `package`, `login`, `a2a-demo`. `make package MODE=a|b` exige árvore limpa e HEAD na `main`.

## Regras
- Relógio só em `core/clock.py` (`reference_today`, `wall_now`, `monotonic`); `test_clock_rule` varre o resto.
- Segredos só por arquivo (`/run/secrets/<consumidor>`), gerados por `scripts/init_secrets.py`; nunca em env/`.env` (exceto `GEMINI_API_KEY`, copiada).
- Números operacionais só em `slo_targets.yaml`.
- Identificadores em inglês; prompt, respostas e docs em PT-BR. Linha de 100 colunas.

## Commits
Conventional Commits em PT-BR, sem trailers e sem menção a IA na mensagem.
