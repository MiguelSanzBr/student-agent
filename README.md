# Agente de atendimento ao aluno

Agente para o aluno já autenticado: consulta e explica vida acadêmica, financeiro, protocolos, LMS e catálogo, em 5 marcas e 4 sistemas. Abre protocolo só como rascunho que o aluno confirma com um clique. Pelo A2A, só consulta e orienta.
A segurança é de arquitetura (RLS forçado, ferramentas sem identidade, texto livre nunca vai ao LLM, `EvidenceGuard`), provada por testes que não dependem do modelo.

## Resultados

<!-- chaves esperadas: quality.dev.pass1, quality.test.pass1, quality.seen.pass1, quality.unseen.pass1, security.holdout.behavior.success, latency.two_plus.p95 -->
| Número (IC 95%) | Desenvolvimento | Teste |
|---|---|---|
| pass@1 (primário) | <!-- num:quality.dev.pass1 -->a medir<!-- /num --> | <!-- num:quality.test.pass1 -->a medir<!-- /num --> |
| pass@1 em templates vistos × não vistos | <!-- num:quality.seen.pass1 -->a medir<!-- /num --> | <!-- num:quality.unseen.pass1 -->a medir<!-- /num --> |
| Ataques de comportamento bem-sucedidos (holdout cego) | | <!-- num:security.holdout.behavior.success -->a medir<!-- /num --> |
| Latência p95, turnos com 2 ou mais ferramentas | | <!-- num:latency.two_plus.p95 -->a medir<!-- /num --> |

Método, limites e o que não foi medido: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Como rodar

```
cp .env.example .env    # GEMINI_API_KEY é opcional; sem ela (`LLM_PROVIDER=auto`, modo local/dev) o agente roda em modo demonstração; `APP_ENV=prod` exige a chave
docker compose up -d --wait
make login CASE=4       # imprime uma URL de login de uso único
make a2a-demo           # cliente A2A: lê o card e faz uma pergunta
```
Sem chave, a interface mostra a faixa "modo demonstração" e sugestões de pergunta. A resposta vem de um roteiro (`app/agent/replay.py`) que chama as ferramentas de verdade e monta o texto com a saída delas; pergunta fora do roteiro recebe uma resposta honesta de que não está no roteiro.

## Evidências

- Requisito, mecanismo, teste e número: [docs/evidence/README.md](docs/evidence/README.md).
- Arquitetura, decisões e trade-offs, com o diagrama: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Vídeo de demonstração: <!-- autor: link do vídeo -->a definir.

<!-- autor: a seção "Como foi construído" (ARCHITECTURE.md, §12) depende da sua escolha de transparência; ver a proposta lá. -->
