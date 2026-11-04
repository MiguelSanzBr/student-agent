# Latência, carga e alavancas (Task 33)

Critérios escritos **antes** de qualquer rodada. Resultados entram abaixo, como saíram, inclusive
quando reprovam. Números operacionais: `slo_targets.yaml` (o YAML vale em caso de divergência).

## Escada (a): limitador e cota simulada

Laço aberto (Poisson, `evals/latency/loadgen.py`), degraus de 0,25× a 2× a cota (cota =
`quota_fraction` × RPM ÷ `admission.est_requests` turnos/s), `LoadTestLLM` no lugar do Gemini,
limitador de admissão ligado. Passa (`ladder_a_pass`) só se **todos** valerem:

1. goodput em platô: o degrau mais alto não cai abaixo de 90% do pico de admitidos;
2. toda rejeição é 503 com `Retry-After`;
3. retries ≤ 10% das requisições, em cada degrau;
4. turnos cortados, por qualquer causa, ≤ 0,5%, em cada degrau;
5. p95 dos admitidos a 2× a cota ≤ 1,1 × p95 do degrau de base (0,25×).

## Escada (b): sem limitador, vagas ilimitadas

Mesma escada, sem o limitador e sem teto de vagas, para achar onde o processo satura sozinho.
Sem critério de aprovação: é descritiva. Registrar o degrau em que o p95 passa de 2× o da base e
a causa (CPU, pool do SQL, laço de eventos). Variantes: 1 container com 1 e com 2 workers, `cpus:`
limitado; a capacidade por worker sai daí (alimenta `capacity()`).

## Janelas de latência (previsto × medido)

| Janela | Quando | n por classe | Critério |
|---|---|---|---|
| A | 1ª rodada dev (Task 31) | o que a rodada der | só amostras: `refit_after_window_a` reajusta lognormais e metas uma única vez; `budget_exceeded` ≤ 0,5% (senão recalcular o orçamento do grafo antes do M4) |
| B | domingo 7h | ≥ 100 | p50, p90, p95, p99 e TTFE p95 de cada classe ≤ meta, com o IC 95% (bootstrap por aluno) reportado; qualquer quantil acima da meta é publicado como falha |
| C | segunda 9h, SHA congelado | ≈ 50 | igual a B, sem ajuste de meta (`frozen: true` no M4) |

`bench.py` reporta por rodada: IC por aluno, cascata por componente, todos os quantis contra as
metas, TTFE por classe, p95 global e o peso de cada classe, cache, custo por turno e taxa de
`budget_exceeded`.

## Alavancas (flags desligadas por padrão)

Ligar só se a medição mostrar ganho; a decisão e o número entram aqui.

| Alavanca | Flag | Critério para ligar |
|---|---|---|
| Hedge da 1ª chamada | `LEVER_HEDGE_FIRST_CALL` | p95 da classe cai ≥ 5% **e** custo por turno sobe ≤ 5% (o custo da perdedora é contado em `hedge_usage`) |
| Frases em `answer_delta` | `LEVER_SENTENCE_STREAM` | TTFE da resposta cai e nenhuma frase sai antes do desfecho do guard (garantido por construção: emitido após `run_turn`) |
| flash-lite × 3.8-flash | `bench.py --model` | só troca de padrão se passar nos critérios da janela B e o pass@1 não cair além do IC |

## Resultados

A preencher (rodadas de sexta 19h–21h, domingo 7h e segunda 9h).
