#!/usr/bin/env bash
# Pacote de entrega: MODE=a|b make package  ->  dist/student-agent.zip
#  a) git archive do HEAD + dist/student-agent.bundle (histórico como está; compactar os commits
#     de documentação antes é passo manual do autor, nada aqui reescreve histórico).
#  b) git archive do HEAD sem docs/superpowers/ e sem histórico (sem bundle).
# Nunca entra: .env, data/, dados.zip, .lanes/, .worktrees/, .superpowers/, segredos, YAML decifrado
# (só os .age do holdout). git archive já omite .git/ e tudo que não é versionado.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${OUT:-dist}"
case "${MODE:-}" in
  a) extra=() ;;
  b) extra=(':(exclude)docs/superpowers') ;;
  *) echo "defina MODE=a (zip + git bundle do histórico) ou MODE=b (zip sem docs/superpowers/ e sem histórico)" >&2; exit 2 ;;
esac
[ -z "$(git status --porcelain)" ] || { echo "árvore suja: commite ou descarte as mudanças antes de empacotar" >&2; exit 1; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse main)" ] || { echo "HEAD não é a main (o zip é do HEAD e o bundle da main): faça checkout da main" >&2; exit 1; }
mkdir -p "$OUT"
rm -f "$OUT/student-agent.zip" "$OUT/student-agent.bundle"
git archive --format=zip --prefix=student-agent/ -o "$OUT/student-agent.zip" HEAD -- . \
  ':(top,exclude)data' ':(top,exclude).env' ':(top,exclude)dados.zip' ':(top,exclude).lanes' ':(top,exclude).worktrees' \
  ':(top,exclude).superpowers' ':(top,exclude)evals/holdout/data' ':(exclude)evals/holdout/*.yaml' ':(exclude)evals/holdout/*.yml' \
  ':(exclude,glob)**/*.pem' ':(exclude,glob)**/*.key' "${extra[@]}"
[ "$MODE" = a ] && git bundle create "$OUT/student-agent.bundle" main  # só a main: sem branches de tarefa/raia
echo "ok: $OUT/student-agent.zip (MODE=$MODE) sha=$(git rev-parse --short HEAD)"
