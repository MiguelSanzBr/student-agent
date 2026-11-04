# SLO e qualidade (spec §9.4)

Fonte única dos números: `slo_targets.yaml` (metas por fórmula; buckets do Prometheus). Metas de
latência e TTFE são em segundos, por classe de ferramentas do turno (`card` = 0, `1`, `2+`).

## Classificação dos turnos (`agent_turns_total{status,channel}`)

| Status | Classificação |
|---|---|
| `ok` | bom |
| `degraded`, `fallback` | bons para disponibilidade; entram no SLO de qualidade |
| `fixed`, `blocked_limit` (limite do aluno), `cancelled` | excluídos |
| `shed` | SLI próprio de disponibilidade sob carga |
| `llm_unavailable`, `budget_exceeded` | ruins |

`SLI de disponibilidade = (ok + degraded + fallback) / (ok + degraded + fallback + llm_unavailable + budget_exceeded)`.

## Latência e TTFE por classe

| Classe | p50 | p90 | p95 | p99 | TTFE p95 |
|---|---|---|---|---|---|
| `card` | 4,19 | 6,77 | 7,9 | 10,46 | 7,9 (= total) |
| `1` | 7,0 | 9,92 | 11,08 | 13,56 | 4,63 |
| `2+` | 11,74 | 16,68 | 18,12 | 21,01 | 4,62 |

Métricas: `agent_turn_seconds{tools_class,llm_calls,guard,model}` e
`agent_first_event_seconds{tools_class}` (POST → 1º `progress`/`answer`). Os números acima são os
de `slo_targets.yaml` hoje (PRIORS; a atualização única vem depois da janela A): em caso de
divergência, o YAML vale.

## Custo

`agent_cost_usd_total` e `agent_tokens_total{kind=input|output|thinking|cached}`. Custo por dia =
`increase(agent_cost_usd_total[1d])`; teto global em `limits.*.global_ceiling_usd` (degrada a 80%,
503 a 100%).

## Qualidade

| Indicador | Meta | Métrica |
|---|---|---|
| Degradado + fallback | ≤ 5% dos turnos | `agent_guard_total{outcome=degraded\|fallback}` / turnos |
| 👎 | ≤ 10% dos feedbacks | `agent_feedback_total{useful=false}` / total |
| Recusa e vazio por domínio | acompanhar (sem meta) | `agent_answer_kind_total{kind=recusa\|vazio,domain}` |
| Funil do rascunho | acompanhar | `agent_draft_total{outcome=exibido\|confirmado\|expirado}` |

`kind` ∈ resposta, recusa, vazio, fora_cobertura, transbordo (5) × `domain` ∈ academico, financeiro,
protocolos, lms, catalogo, cartao, geral (7): no máximo 35 séries.

## Política do 👎

Sem conteúdo em produção. No 👎, as saídas tipadas do turno (guardadas no Redis pelo TTL da
conversa) vão para `agent_rw.turn_output` por 7 dias, com RLS, para investigação por `ops_audit`.
Além disso, o `/feedback` anota o span raiz `chat.turn` no Phoenix (`thumb`). Traces e logs não
levam texto do aluno nem da resposta (só em `app_env=dev` + `limits_profile=eval`).

## Alerta proposto

Disparar se, em janela de 1 h com ≥ 50 turnos, `(degraded + fallback) / turnos > 5%` por 2 janelas
seguidas, ou `llm_unavailable + budget_exceeded` > 1% dos turnos em 15 min, ou p95 de
`agent_turn_seconds` de uma classe acima da meta de `slo_targets.yaml` por 30 min.
