# Agente de atendimento ao aluno

Agente para o aluno já autenticado: consulta e explica vida acadêmica, financeiro, protocolos, LMS e catálogo, em 5 marcas e 4 sistemas. Abre protocolo só como rascunho que o aluno confirma com um clique. Pelo A2A, só consulta e orienta.
A segurança é de arquitetura (RLS forçado, ferramentas sem identidade, texto livre nunca vai ao LLM, `EvidenceGuard`), provada por testes que não dependem do modelo.

## Resultados

Modelo medido: `claude-haiku-5-5` (Anthropic). Gemini e OpenAI têm adaptador, mas não foram medidos. Na dev, o pass@1 foi de <!-- num:quality.dev.r1.pass1 -->49,1% [36,9%; 61,2%]<!-- /num --> para <!-- num:quality.dev.r3.pass1 -->70,4% [59,0%; 80,6%]<!-- /num --> em duas iterações de prompt e para <!-- num:quality.dev.r5.pass1 -->92,0% [85,9%; 96,9%]<!-- /num --> numa terceira, feita depois de ver o teste (desvio declarado). A meta que eu mesmo fixei antes de medir (limite inferior de 85%) foi atingida na dev e não no teste.

O primário do teste cego rodou uma vez no SHA congelado e saiu baixo por um defeito do gabarito: o redator cego escreveu o domínio (`financeiro`) em vez do nome da ferramenta, e todo item novo que exige ferramenta reprova. Fica como rodou. As rodadas seguintes também contam: r2, código final aceitando o domínio como ferramenta (<!-- num:quality.test.r2.pass1 -->73,5% [65,1%; 81,5%]<!-- /num -->); r3, iteração 3, não cega porque as falhas do v1 a guiaram (<!-- num:quality.test.r3.pass1 -->86,2% [79,7%; 92,0%]<!-- /num -->); r4, iteração 3 num holdout novo (v2), escrito às cegas, cifrado e carimbado antes da rodada única. A r4 é o número do agente final. Detalhes e desvios: ARCHITECTURE, seções 7 e 11.

| Número (IC 95%) | Desenvolvimento | Teste |
|---|---|---|
| pass@1 do agente final (dev r5; teste r4, holdout cego v2) | <!-- num:quality.dev.pass1 -->92,0% [85,9%; 96,9%]<!-- /num --> | <!-- num:quality.test.r4.pass1 -->88,0% [82,8%; 92,5%]<!-- /num --> |
| pass@1 em templates não vistos (v2, cegos) | | <!-- num:quality.test.r4.unseen -->89,6% [82,8%; 95,1%]<!-- /num --> |
| pass@1 em templates vistos (alunos novos) | | <!-- num:quality.test.r4.seen -->86,9% [79,7%; 93,2%]<!-- /num --> |
| pass@1 corrigido por 30 rótulos cegos (sensibilidade) | | <!-- num:quality.test.r4.corrected -->80,5% [66,0%; 91,4%]<!-- /num --> |
| pass@1 primário (r1, agente congelado, com o defeito do gabarito) | | <!-- num:quality.test.pass1 -->43,5% [34,0%; 53,4%]<!-- /num --> |
| Ataques de comportamento bem-sucedidos (holdout cego) | | <!-- num:security.holdout.behavior.success -->0/33 templates (Wilson 95%: 0,0% a 10,4%)<!-- /num --> |
| Latência p95, turnos com 2 ou mais ferramentas (janela B, prompt da iteração 2) | | <!-- num:latency.two_plus.p95 -->7,29 s<!-- /num --> |

Ataques de comportamento na dev: <!-- num:security.dev.behavior.success -->2/40 templates (Wilson 95%: 1,4% a 16,5%)<!-- /num -->. Recall do guard no conjunto cego: <!-- num:guard.blind_recall -->91,8% [78,8%; 100,0%]<!-- /num -->, com <!-- num:guard.false_block -->0/43 (0,0% [0,0%; 8,2%])<!-- /num --> bloqueios indevidos nos controles sintéticos; na r1, o guard bloqueou <!-- num:quality.test.guard_blocked_ok -->124 de 3321 itens (3,7%)<!-- /num --> com resposta certa.

Método, limites e o que não foi medido: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). No texto cru, os comentários `<!-- num:… -->` marcam números renderizados por `scripts/render_docs.py` a partir de `docs/evidence/`.

## Como rodar

Pré-requisitos: Docker com Compose v2 e `make` (para os testes, `uv` e Python 3.12). Portas no host, só em 127.0.0.1: 8000 (API), 55432 (Postgres) e 56379 (Redis); troque com `API_PORT`, `DB_PORT` e `REDIS_PORT`.

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

## Como testar

`uv sync && make test` roda os unitários sem banco nem chave (sem `.git` ou sem `data/dados.zip`, os testes que dependem deles são pulados). Integração (RLS, marcador, A2A): `make lane-up LANE=c` e `DB_PORT=55434 REDIS_PORT=56381 LANE_SECRETS_DIR=$PWD/.lanes/c uv run pytest -q tests/integration tests/e2e`; saída em `docs/evidence/security-tests.txt`.

## Evidências

- Requisito, mecanismo, teste e número: [docs/evidence/README.md](docs/evidence/README.md).
- Arquitetura, decisões e trade-offs, com o diagrama: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- Vídeo de demonstração: [docs/evidence/video/demo.webm](docs/evidence/video/demo.webm) (menos de 3 minutos, sem áudio, com painel explicativo; gravado ao vivo contra o app real).
- Como foi construído: [docs/ARCHITECTURE.md, seção 12](docs/ARCHITECTURE.md#12-como-foi-construído).
