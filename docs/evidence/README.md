# Evidências

Cada arquivo de evidência é gerado por script, com um `.json` de números e um `.md` legível. Modelo sob teste: `claude-haiku-5-5`. Rodadas reais publicadas: dev 1 a 3, guard, ataques (dev e holdout cego), teste cego, janelas A, B e C de latência e carga. Os números das tabelas e de capa de `README.md` e `docs/ARCHITECTURE.md` vêm desses JSONs por `scripts/render_docs.py` (que também grava `numbers.json`); números de contexto no texto citam os mesmos arquivos. Os resultados de avaliação são publicados como saíram, inclusive quando ficam abaixo da meta. `make publish-evidence` copia as saídas públicas dos runners (security, latência, escada a) do volume para esta pasta; `python scripts/render_docs.py` renderiza os números (o guard grava em `quality/guard/`).

## Arquivos

| Arquivo | Conteúdo | Estado |
|---|---|---|
| `data-quality` | Contagens da carga (SQL contra memória), resolução por regra, invariantes, cobertura | Gerado |
| `spikes` | Comportamento medido do A2A 1.0, do LangGraph e do Phoenix; versões fixadas | Gerado |
| `quality` | pass@1 com IC, vistos e não vistos, recusa, cruzamento, erro por causa | Dev 1 a 3 e teste gerados (`quality/dev-r{1,2,3}`, `quality/test-r1`, a 2ª rodada `quality/test-r2`, `rounds.jsonl`); sensibilidades do teste em `test-r1/quality.resummarized.*` (validação do scorer) e `test-r1/ferramenta-dominio/` (gabarito, com anexo) |
| `guard` | Recall por tipo, bloqueio indevido, perturbação cega | Gerado (`quality/guard/`); ver a nota sobre o conjunto cego e o viés da seção "rascunhos do dev × scorer" |
| `security` | Testes de arquitetura e ataques com LLM real | Gerado: dev, rodada plantada e holdout cego (`security-blind.md`); rótulos às cegas × juiz em `security-labels.md`; saída do pytest dos testes de arquitetura em `security-tests.txt` |
| `latency` | Previsto contra medido por classe, cascata por componente, carga, custo | `latency.md` traz os critérios, escritos antes das rodadas; `latency-{A,B,C}.json` e escadas de carga `load-{a,b}-w*.json` gerados |
| `conversations.md` | 10 roteiros fixos (financeiro, cruzamento, AVA, rascunho, multiturno, premissa falsa, recusas, A2A) rodados uma vez contra a API real no código final, com rótulo de cada um e as falhas | Gerado (`evals/conversations.py`) |
| `video/demo.webm` | Demo ao vivo (chat, protocolo, recusas, A2A) gravada com Playwright contra o app real; tomadas fora do roteiro foram refeitas, como o próprio vídeo declara | Gerado |
| `observability` | Spans, anotações e métricas | `observability.md` traz o smoke contra um Phoenix real; o export da rodada de teste não foi feito |
| `graph.md` | Grafo gerado por `draw_mermaid` | Gerado |
| `langflow-a2a.json` | Pergunta do golden 4 respondida pelo agente através do fluxo do Langflow (`make langflow-demo`) | Gerado |
| `img/chat-golden4-fake.jpg` | Captura do chat web (aluno do caso 4) | Gerado |
| `img/langflow-a2a-flow.jpg`, `img/langflow-a2a-chat.jpg` | Fluxo Chat Input → A2A Agent → Chat Output no Langflow 1.13.1 e a resposta do agente ao golden 4 no Playground (replay, sem chave) | Gerado |

## Requisito, mecanismo, teste e número

| Requisito | Mecanismo | Teste | Número |
|---|---|---|---|
| Um aluno nunca vê dado de outro | RLS forçado, `person_key` do `sub`, ferramentas sem identidade | `tests/integration/test_rls.py`, `test_compromised_llm.py`; posse nos 11 métodos A2A (`tests/e2e/test_a2a.py::test_ownership_*`) | <!-- num:security.leak.success -->1/20 templates (Wilson 95%: 0,9% a 23,6%)<!-- /num --> vazamentos |
| Nenhum dado corporativo exposto | Destino por coluna no ETL | Varredura por valor do dump de `canon` (`test_canon_dump.py`) | <!-- num:data.logins_absent -->29<!-- /num --> logins de analista ausentes das tabelas carregadas |
| Injeção indireta sem canal | `texto_livre` nunca vai ao LLM; taint fixo | `tests/integration/test_marker_never_trusted.py`: marcador em toda célula de texto, ETL e LLM comprometido, nenhuma ocorrência em saída confiável | Zero por construção |
| Resposta com valor correto | Regras puras e `EvidenceGuard` ancorado no registro | `test_oracle_independence.py`, `test_dataset.py`, `test_perturb_blind.py` | pass@1 <!-- num:quality.test.pass1 -->43,5% [34,0%; 53,4%]<!-- /num --> |
| Cruzamento entre sistemas | Resolução por (marca, RA), contrato e regras de ligação | Contagens por regra e invariantes na carga (`test_resolve_*.py`, `tests/integration/test_full_counts.py`) | <!-- num:data.protocols_linked -->3911<!-- /num --> protocolos ligados, <!-- num:data.protocols_unlinked -->22<!-- /num --> sem vínculo |
| Recusa correta e sem falsa promessa | Prompt fixo, compromisso exige fonte, fallback por domínio | Categorias de ataque (`make eval-security`) e itens de recusa do dataset; `tests/unit/test_score.py` | Recusa indevida <!-- num:quality.test.refusal_wrong -->0,0% [0,0%; 0,0%]<!-- /num --> |
| Abrir protocolo com segurança | Rascunho do servidor com `draft_id` preso ao aluno e de uso único, clique humano, lock, `draft_id UNIQUE` | `tests/integration/test_turn_output.py` (`test_confirm_once_concurrent`, `test_two_drafts_same_service_concurrent_one_created`, chave do lock calculada no SQL e estável entre processos); `tests/e2e/test_confirm_feedback.py` (`draft_id` alheio); `SA-` digitado em `test_evidence_guard.py` | Sem número: teste de comportamento |
| Latência medida e relatada | `slo_targets.yaml`, fórmula pré-registrada, três janelas (A, B, C) | `make eval-latency`; `tests/unit/test_formula.py`, `test_bench.py` | p95 `2+` <!-- num:latency.two_plus.p95 -->7,29 s<!-- /num --> |
| Escala e custo | Admissão RPM+TPM a 90% da cota, vaga por turno, orçamento | Escadas de carga (a) com limitador e cota simulada e (b) sem limitador (`make load-test`); `tests/unit/test_admission.py`, `test_load_test.py` | Custo por turno <!-- num:latency.cost.per_turn -->US$ 0,0008<!-- /num --> |
| Integração com outros agentes | A2A 1.0 sobre o mesmo `TurnService` | `tests/e2e/test_a2a.py`: posse nos 11 métodos, com controle positivo do dono | Código de tarefa alheia <!-- num:a2a.TASK_NOT_FOUND -->-32001<!-- /num --> |
| Depurar sem PII | Traces sem conteúdo, anotações por `trace_id`, saídas tipadas no 👎 | `tests/unit/test_obs.py`, `tests/e2e/test_observability.py` (com `PHOENIX_URL`), `tests/integration/test_turn_output.py` | Ver `observability` |

## Integridade da avaliação cega do guard

Em 2026-11-04, um smoke do runner do guard mediu o conjunto cego real em um volume descartável (código em 3d67e20). O smoke é divulgado com o SHA e sem o número: o número oficial está em `quality/guard/guard.md`. A rodada oficial (semente 20261106, guard congelado) reproduziu o ponto e os controles do smoke. Divergência indicaria que o guard mudou, e então os dois números seriam reportados. A semente do conjunto cego está fixa em `evals/guard/run.py` (`BLIND_SEED`) e o runner grava um marcador de medição única.

## Evidência do fluxo web

![Chat do caso 4 com LLM fake](img/chat-golden4-fake.jpg)

Captura do chat web para o aluno do caso 4, rodada com o LLM fake dos testes. Mostra o fluxo (interface, cartão e resposta), e não o comportamento do LLM real; nenhum número de qualidade sai dela.

## Como as avaliações rodam

Os runners ficam no estágio `eval` do `Dockerfile` e recebem só o `dados.zip` montado. O holdout cego é cifrado com `age` para a chave do autor; o manifesto (`evals/holdout/manifest.json`) guarda o SHA-256 do `.age` e do texto claro, e a rodada de teste os confere antes de começar (`evals/holdout/verify.py`).
