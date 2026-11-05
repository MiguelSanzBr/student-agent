# Latência, carga e alavancas

Histórico: v1 (sexta); v2 (revisão de fase, antes de qualquer rodada): removida a alavanca de frases
em `answer_delta` (o guard precisa da resposta inteira, então não reduz o TTFE), hedge passa a ter
orçamento e a ser contabilizado no balde, e as escadas rodam contra a API (não em processo).

Critérios escritos **antes** de qualquer rodada. Resultados entram abaixo, como saíram, inclusive
quando reprovam. Números operacionais: `slo_targets.yaml` (o YAML vale em caso de divergência).

## Escada (a): limitador e cota simulada

`make load-test LANE=d LADDER=a`: laço aberto (Poisson, `evals/latency/load_test.py`) contra a API
com `LLM_PROVIDER=loadtest` (LoadTestLLM, sem gastar; 3% das chamadas simulam 1 retry), Redis,
`AdmissionBucket` e `TurnSlots` reais, perfil de limites `load` (sem limite por aluno). Degraus de
0,25× a 2× a cota (cota simulada derivada em `load_test.derive_quota` de modo que turnos/s da cota ≤ 0,4 × turnos/s das
vagas = `turn_slots` ÷ média do turno do LoadTestLLM (o p50 subestima: as vagas recusaram a 1×); a derivação vai no JSON). Passa
(`ladder_a_pass`; números em `model_constants.ladder_a`) só se **todos** valerem:

1. goodput em platô: o degrau mais alto não cai abaixo de 90% do pico de admitidos;
2. toda rejeição (503 ou 429) leva `Retry-After`;
3. retries do LLM ≤ 10% das chamadas (delta de `agent_llm_retries_total` ÷ `agent_llm_seconds_count`);
4. turnos cortados, por qualquer causa (status `budget_exceeded`/`llm_unavailable`/erro SSE,
   timeout ou falha de transporte), ≤ 0,5% dos enviados, em cada degrau;
5. p95 dos admitidos a 2× a cota ≤ 1,1 × p95 do degrau de base (0,25×);
6. a 2× a cota há recusas e a maioria é pelo motivo `quota` (`agent_admission_rejected_total{reason}`), não `slots`.

Escada sem admitidos no topo ou vazia reprova.

## Escada (b): sem limitador, vagas ilimitadas

`LADDER=b`: mesmo driver, com `admission.quota_fraction` e `turn_slots` enormes (yaml gerado por
`--write-unlimited`, montado por `ops/compose.load.yml`) e degraus de 0,5× a 8×. Descritiva, sem
critério de aprovação. Registrar o degrau em que o p95 passa de 2× o da base e a causa (CPU, pool
do SQL, laço de eventos). Variantes: `WORKERS=1|2` (uvicorn `--workers`) e `CPUS=` (limite do
container); a capacidade por worker sai daí e alimenta `capacity()`.

## Janelas de latência (previsto × medido)

| Janela | Quando | n por classe | Critério |
|---|---|---|---|
| A | 1ª rodada dev | o que a rodada der | só amostras: `refit_after_window_a` reajusta lognormais e metas uma única vez; `budget_exceeded` ≤ 0,5% (senão recalcular o orçamento do grafo antes do congelamento) |
| B | prompt final | ≥ 100 | p50, p90, p95, p99 e TTFE p95 de cada classe ≤ meta, com o IC 95% (bootstrap por aluno) reportado; classe sem amostra reprova; qualquer quantil acima da meta é publicado como falha |
| C | SHA congelado | ≈ 50 | igual a B, sem ajuste de meta (`frozen: true` no congelamento) |

`make eval-latency` (runner HTTP real contra a API em perfil eval, um turno por vez; `--allow-sim` só
ensaia o código e marca `"simulated": true`) reporta por rodada: IC por aluno, cascata por componente, todos os quantis contra as
metas, TTFE por classe, p95 global e o peso de cada classe, cache, custo por turno e taxa de
`budget_exceeded`.

## Alavancas (flags desligadas por padrão)

Ligar só se a medição mostrar ganho; a decisão e o número entram aqui.

| Alavanca | Flag | Critério para ligar |
|---|---|---|
| Hedge da 1ª chamada | `LEVER_HEDGE_FIRST_CALL` | p95 da classe cai ≥ 5% **e** custo por turno sobe ≤ 5%; a 2ª requisição entra no balde, na cota diária e no gasto (estimativa = uso da vencedora) e só dispara dentro de `hedge.max_fraction` (piso `floor_per_min`) e com o balde abaixo de `hedge.bucket_max_util` |
| flash-lite × 3.8-flash | `--model` (= `LLM_MODEL` da API) | só troca de padrão se passar nos critérios da janela B e o pass@1 não cair além do IC |

O atraso do hedge é o p90 da 1ª chamada como mistura de `answer_call` (turnos `card`, peso
`hedge.card_share`) e `tool_call`; só `tool_call` dispararia em ~45% dos `card`.

O mix de classes do bench sob LoadTestLLM só vale como fumaça (a classe vem do evento `debug`, e o
fake sorteia as ferramentas); as janelas A/B/C rodaram com `claude-haiku-5-5`.

## Resultados

Rodadas reais de 2026-11-04 com `claude-haiku-5-5`; resumo e leitura em `docs/ARCHITECTURE.md`, seção 8.

| Arquivo | O que é | n (card / 1 / 2+) |
|---|---|---|
| `latency-A.json` | janela A, antes das iterações de prompt; base do reajuste único | 124 / 58 / 118 |
| `latency-B.json` | janela B, prompt final | 72 / 71 / 157 |
| `latency-C.json` | janela C, SHA congelado | 43 / 14 / 93 |
| `load-a-w{1,2}.json`, `load-b-w{1,2}.json` | escadas de carga com `LoadTestLLM` (1 e 2 workers) | |

Desvios em relação aos critérios acima: B e C ficaram abaixo de 100 turnos nas classes `card` e `1` (a mistura de classes vem das perguntas, não é controlada), então o p99 por classe é indicativo; e o guard degradou 20%, 10% e 17% dos turnos nas janelas A, B e C, contra os 5% (`p_guard_retry`) da fórmula. A carga mede o app com LLM falso, não o provedor.
