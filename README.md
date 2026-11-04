# Agente de atendimento ao aluno

Agente para o aluno já autenticado: consulta e explica vida acadêmica, financeiro, protocolos, LMS e catálogo, em 5 marcas e 4 sistemas. Abre protocolo só como rascunho que o aluno confirma com um clique. Pelo A2A, só consulta e orienta.
A segurança é de arquitetura (RLS forçado, ferramentas sem identidade, texto livre nunca vai ao LLM, `EvidenceGuard`), provada por testes que não dependem do modelo.

## Resultados

Modelo medido: `claude-haiku-5-5` (Anthropic). Gemini e OpenAI têm adaptador, mas não foram medidos. O teste cego ainda não rodou ("a medir"). Na dev, o pass@1 foi de <!-- num:quality.dev.r1.pass1 -->49,1% [36,9%; 61,2%]<!-- /num --> para <!-- num:quality.dev.r2.pass1 -->62,4% [49,6%; 74,5%]<!-- /num --> e <!-- num:quality.dev.r3.pass1 -->70,4% [59,0%; 80,6%]<!-- /num --> em duas iterações de prompt. A meta (limite inferior de 85%) não foi atingida.

<!-- chaves esperadas: quality.dev.pass1, quality.test.pass1, quality.seen.pass1, quality.unseen.pass1, security.holdout.behavior.success, latency.two_plus.p95 -->
| Número (IC 95%) | Desenvolvimento | Teste |
|---|---|---|
| pass@1 (primário) | <!-- num:quality.dev.pass1 -->70,4% [59,0%; 80,6%]<!-- /num --> | <!-- num:quality.test.pass1 -->a medir<!-- /num --> |
| pass@1 em templates vistos × não vistos | <!-- num:quality.seen.pass1 -->a medir<!-- /num --> | <!-- num:quality.unseen.pass1 -->a medir<!-- /num --> |
| Ataques de comportamento bem-sucedidos (holdout cego) | | <!-- num:security.holdout.behavior.success -->a medir<!-- /num --> |
| Latência p95, turnos com 2 ou mais ferramentas | | <!-- num:latency.two_plus.p95 -->7,34 s<!-- /num --> |

Ataques de comportamento na dev: <!-- num:security.dev.behavior.success -->2/40 templates (Wilson 95%: 1,4% a 16,5%)<!-- /num -->. Recall do guard no conjunto cego: <!-- num:guard.blind_recall -->91,8% [78,8%; 100,0%]<!-- /num -->, com <!-- num:guard.false_block -->0/43 (0,0% [0,0%; 8,2%])<!-- /num --> bloqueios indevidos.

Método, limites e o que não foi medido: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Como rodar

```
mkdir -p data && cp /caminho/do/case/dados.zip data/dados.zip   # o zip do case; não vai no repositório
cp .env.example .env    # a chave de um dos três provedores (`ANTHROPIC_API_KEY`, `GEMINI_API_KEY` ou `OPENAI_API_KEY`; com várias, `LLM_PROVIDER=auto` escolhe nesta ordem) é opcional; sem nenhuma (`LLM_PROVIDER=auto`, modo local/dev) o agente roda em modo demonstração; `APP_ENV=prod` exige uma
docker compose up -d --wait
docker compose run --rm etl   # carga única: cria o banco e grava os logins dos casos golden
make login CASE=4       # imprime uma URL de login de uso único
make a2a-demo           # cliente A2A: lê o card e faz uma pergunta
make langflow-demo      # Langflow (imagem fixada) chama o agente por A2A; KEEP=1 deixa a UI em http://127.0.0.1:7860
```
Sem chave, a interface mostra a faixa "modo demonstração" e sugestões de pergunta. A resposta vem de um roteiro (`app/agent/replay.py`) que chama as ferramentas de verdade e monta o texto com a saída delas; pergunta fora do roteiro recebe uma resposta honesta de que não está no roteiro.

## Evidências

- Requisito, mecanismo, teste e número: [docs/evidence/README.md](docs/evidence/README.md).
- Arquitetura, decisões e trade-offs, com o diagrama: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Vídeo de demonstração: <!-- autor: link do vídeo -->a definir.

<!-- autor: a seção "Como foi construído" (ARCHITECTURE.md, §12) depende da sua escolha de transparência; ver a proposta lá. -->
