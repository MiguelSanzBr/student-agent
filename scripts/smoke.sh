#!/bin/sh
# make smoke LANE=x: fixture como data/, LLM fake, login do golden 4, 1 turno (R$ 49.508,43), 503.
set -eu
: "${DC:?use make smoke}" "${PORT:?}"
trap '$DC down -v >/dev/null 2>&1 || true' EXIT
base="http://127.0.0.1:$PORT"
jar=$(mktemp)
export DATA_DIR="$PWD/tests/fixtures/mini" LLM_PROVIDER=fake DEV_LOGIN=true
$DC up -d --build --wait init-secrets db redis
$DC run --rm etl python -m etl --source /data
$DC up -d --build --wait api
ticket_url=$($DC exec -T -e PUBLIC_BASE_URL="$base" api python -m scripts.login_url 4)
curl -fsS -c "$jar" -o /dev/null "$ticket_url"
out=$(curl -fsS -b "$jar" -H 'content-type: application/json' \
  -d '{"conversation_id":"smoke","text":"quanto falta para quitar?"}' "$base/chat")
echo "$out" | grep -q 'R\$ 49.508,43' || { echo "smoke: resposta sem R\$ 49.508,43:" >&2; echo "$out" >&2; exit 1; }
# 503: teto global estourado (US$ 20 em micro-US$) -> recusa antes do stream, com Retry-After
$DC exec -T redis sh -c 'REDISCLI_AUTH="$(cat /run/secrets/redis/redis_password)" redis-cli set "sa:spend:global:-:$(date -u +%Y%m%d)" 20000000 >/dev/null'
code=$(curl -s -b "$jar" -D "$jar.h" -o /dev/null -w '%{http_code}' -H 'content-type: application/json' \
  -d '{"conversation_id":"smoke","text":"oi"}' "$base/chat")
[ "$code" = 503 ] && grep -qi '^retry-after:' "$jar.h" || { echo "smoke: esperado 503 + Retry-After, veio $code" >&2; exit 1; }
# a API não enxerga os dados nem o segredo do ETL
! $DC exec -T api sh -c 'test -e /data || test -e /run/secrets/etl' || { echo "smoke: api enxerga /data ou /run/secrets/etl" >&2; exit 1; }
rm -f "$jar" "$jar.h"
echo "smoke ok"
