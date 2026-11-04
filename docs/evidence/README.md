# Evidências

<!-- rascunho: a coluna "Teste" e os arquivos de resultado dependem das Tasks 11 a 33; conferir cada caminho contra o repositório antes da entrega -->

Cada arquivo de evidência é gerado por script e tem um `.json` com os números e um `.md` legível. Os números de `README.md` e de `docs/ARCHITECTURE.md` vêm desses JSONs por `scripts/render_docs.py` (que também grava `numbers.json`); nenhum é digitado à mão. Os resultados de avaliação são publicados como saíram, inclusive quando ficam abaixo da meta.

## Arquivos

| Arquivo | Conteúdo | Estado |
|---|---|---|
| `data-quality` | Contagens da carga (SQL contra memória), resolução por regra, invariantes, cobertura | Gerado |
| `spikes` | Comportamento medido do A2A 1.0, do LangGraph e do Phoenix; versões fixadas | Gerado; o Gemini está pendente por falta de chave |
| `quality` | pass@1 com IC, vistos e não vistos, recusa, cruzamento, erro por causa | A gerar |
| `guard` | Recall por tipo, bloqueio indevido, perturbação cega | A gerar |
| `security` | Testes de arquitetura e ataques com LLM real | A gerar |
| `latency` | Previsto contra medido por classe, cascata por componente, carga, custo | A gerar |
| `conversations` | Roteiros do replay e do vídeo, falhas rotuladas | A gerar |
| `observability` | Spans, anotações e métricas da rodada de teste | A gerar |
| `graph.md` | Grafo gerado por `draw_mermaid` | A gerar |
| `phoenix/` | Export dos spans e anotações da rodada de teste | A gerar |

## Requisito, mecanismo, teste e número

| Requisito | Mecanismo | Teste | Número |
|---|---|---|---|
| Um aluno nunca vê dado de outro | RLS forçado, `person_key` do `sub`, ferramentas sem identidade | Teste de RLS e do LLM comprometido; IDOR nos 11 métodos A2A | <!-- num:security.leak.success -->a medir<!-- /num --> vazamentos |
| Nenhum dado corporativo exposto | Destino por coluna no ETL | Varredura por valor do dump de `canon`; marcador em toda célula de texto | <!-- num:data.logins_absent -->29<!-- /num --> logins de analista ausentes das tabelas carregadas |
| Injeção indireta sem canal | `texto_livre` nunca vai ao LLM; taint fixo | Teste do marcador: ETL, LLM comprometido, nenhuma ocorrência em saída confiável | Zero por construção |
| Resposta com valor correto | Regras puras e `EvidenceGuard` ancorado no registro | Oráculo independente; dataset de avaliação; perturbação cega | pass@1 <!-- num:quality.test.pass1 -->a medir<!-- /num --> |
| Cruzamento entre sistemas | Resolução por (marca, RA), contrato e regras de ligação | Contagens por regra e invariantes na carga | <!-- num:data.protocols_linked -->3911<!-- /num --> protocolos ligados, <!-- num:data.protocols_unlinked -->22<!-- /num --> sem vínculo |
| Recusa correta e sem falsa promessa | Prompt fixo, compromisso exige fonte, fallback por domínio | Categorias de ataque e perguntas fora do escopo | Recusa indevida <!-- num:quality.test.refusal_wrong -->a medir<!-- /num --> |
| Abrir protocolo com segurança | Rascunho do servidor, clique humano, lock, `draft_id UNIQUE` | Confirmação concorrente; token alheio; `SA-` digitado | Teste de dois processos |
| Latência medida e relatada | `slo_targets.yaml`, fórmula pré-registrada, quatro janelas | Bench por classe, previsto contra medido | p95 `2+` <!-- num:latency.two_plus.p95 -->a medir<!-- /num --> |
| Escala e custo | Admissão RPM+TPM a 90% da cota, vaga por turno, orçamento | Escada de carga com cota simulada | Custo por turno <!-- num:latency.cost.per_turn -->a medir<!-- /num --> |
| Integração com outros agentes | A2A 1.0 sobre o mesmo `TurnService` | Posse nos 11 métodos, com controle positivo | Código de tarefa alheia <!-- num:a2a.TASK_NOT_FOUND -->-32001<!-- /num --> |
| Depurar sem PII | Traces sem conteúdo, anotações por `trace_id`, saídas tipadas no 👎 | Testes de observabilidade | Ver `observability` |
