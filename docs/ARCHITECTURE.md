# Arquitetura do agente de atendimento ao aluno

Este documento explica o que foi decidido, por que, e o que custa. Vale para o escopo do case: um agente para o aluno **já autenticado**, sobre 5 marcas, 4 sistemas (SAG, LMS, secretaria, financeiro) e cerca de <!-- num:data.persons -->1000<!-- /num --> alunos, com data de referência 31/10/2026.

O agente consulta, explica e orienta sobre vida acadêmica, financeiro, protocolos, LMS e catálogo. Ele abre protocolos só como rascunho, que o próprio aluno confirma com um clique. Pelo A2A ele só consulta e orienta.

A tese que organiza tudo: **o que precisa ser garantido não pode depender do comportamento do modelo.** Isolamento entre alunos, ausência de dado corporativo, limites de capacidade e correção de valores são da arquitetura, testados sem LLM. O modelo é medido onde ele realmente decide: roteamento, redação e recusa.

## 1. Visão geral

```mermaid
flowchart LR
  U[Chat web / cliente A2A] -- "cookie aud=chat / Bearer ou x-api-key aud=a2a" --> API[FastAPI]
  API --> ADM[admit: balde RPM+TPM global + vaga por turno]
  ADM --> TS[TurnService<br/>limites · histórico · taint]
  TS --> CARD[Cartão do aluno]
  TS --> G[LangGraph StateGraph]
  G -->|LLM| GEM[LLM · Gemini / Claude / GPT]
  G -->|TOOL| T[Ferramentas tipadas] --> PG[(Postgres<br/>canon RLS FORCE · agent_rw · util)]
  G -->|GUARDRAIL| EG[EvidenceGuard] --> OUT[SSE / tarefa A2A]
  API -.-> OBS[OTel → Phoenix · Prometheus]
  SEC[init-secrets] -.-> API
  ETL[ETL one-shot] --> PG
```

Um turno funciona assim:

1. A API autentica o JWT (audiência por canal) e chama `admit()` **antes** do stream; sem cota, 429 ou 503 com `Retry-After`.
2. O `TurnService` monta a requisição: prompt fixo, 11 ferramentas fixas e o **cartão do aluno** como resposta sintética de `get_student_card`.
3. O grafo alterna `llm` e `tools`; cada ferramenta abre conexão própria, já com o aluno no RLS.
4. O EvidenceGuard confere o texto. Reprovado, o modelo tenta de novo; reprovado outra vez, as frases sem evidência saem ou vira um fallback dos registros.
5. Chat e A2A usam o mesmo `TurnService` e os mesmos limites.

## 2. As dez decisões centrais

Cada uma responde a uma pergunta da banca. As demais estão na seção 3.

### D1. Ferramentas tipadas sobre um modelo canônico


**Pergunta difícil:** por que não deixar o modelo escrever SQL, que seria mais flexível?

**Decisão.** O agente tem 11 ferramentas com parâmetros enumerados (`enrollment` é "1", "2" ou "3"; `status` e `kind` são enums). Nenhuma recebe identidade: quem fala é o `sub` do JWT, injetado no servidor. Cada ferramenta devolve registros tipados (`EntityRecord`) e valores já formatados (`R$ 1.234,56`, `dd/mm/aaaa`).

**Alternativas.** Text-to-SQL e um híbrido (ferramentas mais SQL livre para a cauda).

**Trade-off.** Perguntas fora do desenho das ferramentas não têm resposta, e o agente diz isso e oferece "Falar com a secretaria". Em troca, a superfície de ataque é enumerável: o teste do "LLM comprometido" exercita toda ferramenta com argumentos fuzzados, o que é inviável com SQL livre. A regra de dívida, de elegibilidade e de prazo mora em código puro, testável contra um oráculo, e não numa conta que o modelo refaz.

### D2. LangGraph chamando os SDKs dos provedores direto


**Pergunta difícil:** por que um grafo, e por que não um framework de agentes completo?

**Decisão.** Um `StateGraph` explícito com três nós (`llm`, `tools`, `guard`; a degradação é uma função do `guard`) que chama os SDKs do Gemini, Anthropic e OpenAI atrás de um contrato `LLMProvider`. Sem checkpointer: o histórico fica no Redis, com chave do servidor. Sem `interrupt()`: a confirmação humana acontece fora do grafo (D8).

**Alternativas.** Loop próprio; `AgentExecutor` do LangChain; `langchain-google-genai`.

**Trade-off.** O grafo dá um desenho publicável (`draw_mermaid`) e eventos de progresso. Ligar o SDK direto evita uma camada que esconderia `thought_signature`, contagem de tokens de cache e raciocínio, e timeouts por chamada, justamente o que a latência e o custo exigem. O raciocínio (assinatura do Gemini, blocos do Claude com `drop_block`, itens cifrados do GPT) volta só ao mesmo modelo. O custo é manter três adaptadores, com a política de retry comum em `app/agent/retry.py`.

### D3. Isolamento em camadas

**Pergunta difícil:** como garantir que um aluno nunca vê dado de outro, mesmo se o código errar ou o modelo for manipulado?

**Decisão.** Cinco camadas independentes:

- o ETL não carrega o que é interno; o destino é decidido **por coluna** (401 colunas, cada uma com destino, tipo e motivo);
- RLS forçado, roles sem superusuário e grants conferidos contra uma allowlist (`agent_app` sem TRUNCATE nem DELETE);
- `app.person_key` vem sempre do `sub` do JWT, nunca de argumento;
- nenhuma policy ou função de `agent_rw` referencia o schema `canon` (os helpers ficam em `util`), então a recarga do ETL não derruba a segurança;
- a API aborta na subida se a própria role for superusuária ou tiver bypass.

**Alternativa.** Filtro por aluno na camada de aplicação.

**Trade-off.** RLS forçado complica a carga: antes do COPY o RLS bloquearia o dono, e depois do FORCE o dono lê 0 linhas. Por isso a carga mede as contagens por SQL antes de aplicar `rls.sql`. Um `WHERE` esquecido devolve zero linhas, não dados alheios.

### D6. EvidenceGuard determinístico


**Pergunta difícil:** como evitar que o agente invente um valor, uma data ou uma promessa, sem pagar um segundo LLM?

**Decisão.** Um verificador sem modelo que extrai afirmações do texto gerado e as ancora no **registro da entidade citada**. Confere valores, datas, percentuais, identificadores, status, polaridade ("não há débitos"), ações ("vou abrir") e compromissos (parcelamento, desconto), que só valem com orientação de serviço ou acordo ativo. "Dívida" e "saldo" ancoram no total para quitar. O que o aluno escreveu só vale se foi argumento de ferramenta que devolveu registro; uma ação só vale com um `SA-` vindo do sistema.

**Alternativas.** Só prompt; um LLM verificador.

**Trade-off.** É barato (sem chamada extra, sem ~1 s a mais) e auditável, mas tem limites declarados: um valor certo atribuído à entidade errada **quando a frase não cita entidade nenhuma** não é pego. Há risco de bloquear resposta correta, por isso o guard é medido nos dois sentidos: recall num conjunto de perturbação cego e bloqueio indevido nas rodadas reais. A degradação é por tipo, e o fallback é pelo domínio: uma pergunta de LMS nunca recebe o resumo financeiro.

**Integridade da medição cega.** Um smoke de 2026-11-04 (3d67e20) mediu o conjunto cego, divulgado sem o número; a rodada oficial, com o guard congelado, reproduziu o ponto.

### D8. Escrita restrita


**Pergunta difícil:** o agente abre protocolos. Como impedir que um texto malicioso faça ele abrir o que não deve?

**Decisão.** O modelo nunca abre nada. Ele pode **preparar um rascunho** com `draft_protocol`, e só passam serviços de uma allowlist e que o aluno pode pedir (elegibilidade por regra: por exemplo, diploma só se formado e sem diploma emitido; revisão de nota e segunda chamada checadas por disciplina). O cartão do rascunho é montado pelo servidor; o rascunho fica no Redis preso ao aluno, com um `draft_id` (uuid4) que vai por um evento próprio do SSE e nunca é serializado para o modelo. A confirmação é um clique no chat web, que consome o `draft_id` uma vez (GETDEL), revalida serviço e elegibilidade, toma um `pg_advisory_xact_lock(hashtextextended(...))` calculado no SQL e faz um INSERT idempotente (`draft_id UNIQUE`). Se a conversa já teve texto livre de sistema (taint), `draft_protocol` é recusado na execução.

**Alternativas.** O modelo abre direto; `interrupt()` do LangGraph; confirmação pelo A2A.

**Trade-off.** O aluno dá um clique a mais e o agente não "resolve" sozinho. Pelo A2A não é possível garantir que quem confirma é uma pessoa, então a escrita simplesmente não existe nesse canal.

### D11. Avaliação com oráculo independente e holdout cego


**Pergunta difícil:** como saber que a resposta sai certa, e não apenas que os exemplos que escolhi saem certos?

**Decisão.**

- Um **oráculo** lê o CSV cru só com a biblioteca padrão, sem importar `core/`, `etl/` nem `app/`, e passa em dois autotestes (a resposta ideal passa; com um fato trocado, reprova).
- O dataset tem pelo menos 50 templates de pergunta, e pelo menos 15 são `test_only`: escritos por um redator cego, que não vê prompt, ferramentas nem guard, e cifrados com `age` para a chave do autor.
- O **pré-registro** (hashes do dataset, do plano de análise e do scorer, em `evals/golden/dataset.sha256`) foi carimbado no OpenTimestamps em 2026-11-04, já com confirmação no Bitcoin; a nota do §18 do plano, anterior a isso, ficou como estava porque o plano está no hash.
- A métrica primária é o pass@1 do scorer determinístico na **primeira execução** no SHA congelado, com IC de 95% por bootstrap de dois fatores (aluno e template), estratificado pelas cotas. A meta é sobre o **limite inferior**.
- O scorer é validado com 30 respostas rotuladas às cegas (por um agente de IA, não pelo autor: desvio, seção 11), em VPP e VPN; o pass@1 corrigido é sensibilidade.
- Toda reprovação é atribuída pelo trace a um estágio (roteamento, dados, redação, guard bloqueou resposta certa, recusa indevida, gabarito).

**Alternativas.** Conversas escolhidas a dedo; LLM-juiz como métrica principal.

**Trade-off.** É caro de montar e a amostra é enriquecida por cotas (IC estratificado). Um juiz como métrica herdaria os vieses do modelo; ele mediria só a concordância com o scorer, e não rodou. A lição do teste: o build não validava `expected_tools` do holdout contra os nomes das ferramentas, e o defeito só apareceu depois da rodada.

### D15. A2A 1.0 sobre o mesmo TurnService


**Pergunta difícil:** como oferecer o agente a outros agentes (por exemplo, Langflow ou ADK) sem abrir uma porta lateral para os limites e para os dados?

**Decisão.** Um servidor A2A 1.0 (JSON-RPC, sem modo de compatibilidade 0.3) sobre o `TurnService`, com Agent Card declarando só consulta e orientação. O `RedisTaskStore` guarda cada tarefa com o dono e um TTL, e filtra por dono em toda leitura. Admissão recusada vira tarefa `REJECTED` com `metadata.retry_after_s`. O `contextId` é ligado ao dono: o de outro aluno cria uma conversa nova e vazia do próprio chamador.

O spike com o cliente real do SDK mostrou: tarefa alheia devolve `TaskNotFound` (<!-- num:a2a.TASK_NOT_FOUND -->-32001<!-- /num -->) e `ListTasks` alheio vem vazio; um `contextId` alheio é **aceito** pelo SDK, então o isolamento da conversa é do executor; e exceções viram `InternalError` com a mensagem original, por isso há wrappers com mensagem fixa e o logger `a2a` silenciado.

**Alternativa.** Só HTTP próprio.

**Trade-off.** O A2A é mais protocolo do que um endpoint simples, e o SDK tem arestas (o `GetExtendedAgentCard` com o card declarado devolve <!-- num:a2a.GetExtendedAgentCard_com_extended_agent_card_False -->-32004<!-- /num -->, e não o <!-- num:a2a.EXTENDED_CARD_NOT_CONFIGURED -->-32007<!-- /num --> que a leitura da especificação sugeria). Em troca, os 11 métodos têm teste de posse (`tests/e2e/test_a2a.py`).

### D16. Observabilidade que liga qualidade e operação


**Pergunta difícil:** quando uma resposta sai errada, como descobrir por quê sem gravar a conversa do aluno?

**Decisão.** Um trace por turno (AGENT, LLM, TOOL, GUARDRAIL) com atributos OpenInference e `gen_ai.*`, inclusive tokens de cache e de raciocínio. Os spans carregam argumentos **enumerados** das ferramentas e contagens, **sem conteúdo**; o conteúdo só existe nos perfis de desenvolvimento e avaliação, com dados sintéticos. O `session.id` é um HMAC da conversa. Em produção, quando o aluno marca 👎, só as **saídas tipadas** do turno vão para `agent_rw.turn_output` por 7 dias, com RLS, para investigação por uma role de auditoria (`make investigate`).

**Alternativa.** Só logs.

**Trade-off.** Sem conteúdo, depurar um caso exige reconstruir a resposta a partir das saídas tipadas e dos kinds do guard, o que é mais trabalhoso do que ler a conversa. É o preço de não guardar PII.

### D17. Capacidade e custo


**Pergunta difícil:** "escalável" significa o quê, quando o gargalo real é a cota do modelo?

**Decisão.** `TurnService.admit()` roda **antes** do stream: reserva requisições e tokens num balde RPM+TPM global (Lua no Redis, a 90% da cota) e uma **vaga por turno** com TTL; sem cota, 503 com `Retry-After`. O `settle()` roda sempre (`finally` sob `asyncio.shield`). O deadline do turno limita cada chamada. Há cota diária por aluno e teto global de gasto: a 80%, menos rodadas e sem retry do guard; a 100%, 503.

**Alternativa.** Ilimitado.

**Trade-off.** Recusar cedo, com `Retry-After`, é pior para o aluno do que enfileirar, mas enfileirar estoura o deadline. O limitador **nunca** corta um turno no meio. A conta de capacidade está na seção 8.

### D21. Cartão do aluno como chamada sintética de ferramenta


**Pergunta difícil:** como responder a maior parte das perguntas com uma única chamada ao modelo, mantendo o cache do prefixo?

**Decisão.** Um resumo do aluno de até 400 tokens, sem nome (matrículas, total para quitar, próximo vencimento, protocolos abertos, próxima avaliação, cobertura), vai como `functionResponse` de uma chamada sintética a `get_student_card`. Prompt e as 11 ferramentas são iguais para todos, o que preserva o cache do provedor.

**Alternativas.** Só ferramentas (uma chamada a mais em toda pergunta); cartão como texto da mensagem do usuário.

**Trade-off.** Um `functionResponse` não pode ser forjado pelo aluno, que um texto de usuário poderia imitar. Com o Claude funciona (todas as rodadas usaram o cartão); no Gemini, que pode exigir a função declarada e a assinatura de pensamento, segue sem teste por falta de chave.

## 3. Decisões menores

| # | Decisão | Alternativa | Por que não é central |
|---|---|---|---|
| D4 | Ontologia em YAML: destino e tipo das 401 colunas, rótulos PT, listas exatas, léxicos do guard | Allowlists espalhadas | Consequência de D3 e D9: é a fonte única de testes e documentação |
| D5 | Sem RAG vetorial: o catálogo pequeno vai inteiro, em PT | pgvector, `pg_trgm` | Não há texto não estruturado para recuperar |
| D7 | Sem classificador de jailbreak | Classificador | Probabilístico e custa cerca de 1 s; a avaliação mede o comportamento |
| D9 | Texto tipificado no ETL: provas, salas e mensagens viram tipos conhecidos; o resto é `texto_livre` e nunca vai ao LLM | Marcar tudo como não confiável | É o mecanismo de D3 contra injeção indireta; entra no taint |
| D10 | SSE com progresso, resposta já checada e heartbeat; cancelamento ao desconectar | Streaming de tokens | O guard precisa do texto inteiro |
| D14 | `person_key` = HMAC-SHA256(segredo, CPF) | UUIDv5 | UUIDv5 é reversível por quem conhece o CPF |
| D19 | `LLM_PROVIDER=auto`: sem chave, replay roteirizado que chama as ferramentas de verdade (`app/agent/replay.py`) | Exigir chave | Quem avalia roda sem chave (modo local/dev). `app_env=prod` exige um provedor real com chave e limites default |
| D23 | `init-secrets` gera todos os segredos, um volume por consumidor, lidos só de arquivo | `.env` de exemplo | Segredo público permite forjar JWT |
| D24 | Cobertura por tipo de dado, derivada pelo ETL | Responder 0 | "Zero" é diferente de "sem dados": o agente diz "não há dados antes de dd/mm/aaaa" |

## 4. Grafo do agente


```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	llm(llm)
	tools(tools)
	guard(guard)
	__end__([<p>__end__</p>]):::last
	__start__ --> llm;
	guard -.-> __end__;
	guard -.-> llm;
	llm -.-> __end__;
	llm -.-> guard;
	llm -.-> tools;
	tools --> llm;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Pontos que o diagrama não mostra:

- O diagrama é a saída de `draw_mermaid()` (`docs/evidence/graph.md`). O nó `tools` recalcula o `tainted` a cada rodada e aplica `Tool.allowed`. O laço `llm` e `tools` vai até `max_rounds` (4; 2 sob custo); a última rodada e o retry do guard vão sem ferramentas.
- Com rascunho emitido, o turno faz mais uma chamada ("responda só ao resto da mensagem, ou nada"); a resposta é o texto fixo do servidor mais esse resto, e **só o resto** passa pelo guard.
- Textos do servidor (rascunho, "Solicitação SA-… aberta", fallback, recusas fixas) não passam pelo guard.

## 5. Dados

### 5.1 Princípios

As fontes da verdade são: SAG para status e notas; faturas e encargos para dívida (o `receivable_snapshot` serve só de gabarito externo); secretaria para protocolos; LMS para atividade, sempre apresentada como nota "não oficial". "Hoje" vem de `reference_today()` e instantes sem fuso do SAG são tratados como America/Sao_Paulo.

O ETL recria o schema `canon` a cada carga, numa transação: cria as tabelas, faz COPY, **compara contagens SQL com as contagens em memória**, gera o relatório e só então aplica o RLS. O schema `agent_rw` é persistente e não tem FK para `canon`: a integridade é checada na aplicação, porque `DROP SCHEMA canon CASCADE` derrubaria a FK e a policy.

### 5.2 Resolução de entidades

Os quatro sistemas não compartilham chave. A resolução foi verificada nos CSV e contra um oráculo independente:

- **Pessoa:** pelo CPF; o `PERSON_ID` não serve (15 CPFs têm dois).
- **Matrícula:** (marca, RA) com dígito verificador mod 11. Nunca só o RA, porque há 173 RAs repetidos em 2 a 5 marcas EAD (mesma fonte).
- **LMS:** SSO por tenant e padrão `PRES_nnnnn` ou `EAD_nnnnn`, mais as contas manuais ligadas por `user_loggedin`. Resultado: <!-- num:data.lms_accounts -->1003<!-- /num --> contas. Contas de professor, administração, integração e `aluno.teste*` não entram em contagem nenhuma.
- **Financeiro:** pelo contrato da matrícula (<!-- num:data.contracts -->1065<!-- /num --> contratos resolvidos), nunca por `taxId` nem `customerId`.
- **Secretaria:** cinco regras em ordem de confiança.

| Regra de ligação do protocolo | Protocolos |
|---|---|
| `ra_cpf` | <!-- num:data.rule.ra_cpf -->3455<!-- /num --> |
| `guardian_payer` (Stu_ID válido, solicitante pagador) | <!-- num:data.rule.guardian_payer -->73<!-- /num --> |
| `guardian_payer_unique` (sem Stu_ID, o responsável paga uma única matrícula na marca) | <!-- num:data.rule.guardian_payer_unique -->29<!-- /num --> |
| `cpf_brand_unique` | <!-- num:data.rule.cpf_brand_unique -->337<!-- /num --> |
| `cpf_brand_by_date` | <!-- num:data.rule.cpf_brand_by_date -->17<!-- /num --> |
| **Ligados** | <!-- num:data.protocols_linked -->3911<!-- /num --> |
| **Sem vínculo** | <!-- num:data.protocols_unlinked -->22<!-- /num --> |

O invariante conferido é que o solicitante é o aluno ou um pagador do contrato. As referências cruzadas do SAG para a secretaria (`PROTOCOL_REF`) batem <!-- num:data.protocol_ref -->229<!-- /num --> de <!-- num:data.protocol_ref -->229<!-- /num -->, e o snapshot financeiro confere <!-- num:data.snapshot -->273<!-- /num --> de <!-- num:data.snapshot -->273<!-- /num -->. Na carga, as contagens SQL e em memória são iguais em todas as tabelas, e os <!-- num:data.logins_absent -->29<!-- /num --> logins de analista que existem nos dados de origem estão ausentes de toda tabela carregada.

### 5.3 Escolhas de precisão

**Na dúvida, não liga.** Há casos que outro sinal resolveria, mas que ficam sem vínculo de propósito: dois `Stu_ID` com erro de digitação, e responsáveis que pagam duas matrículas e abrem protocolo sem dizer de qual. Ligar errado mostraria o protocolo de uma pessoa a outra; não ligar só o esconde. Os <!-- num:data.protocols_unlinked -->22<!-- /num --> protocolos sem vínculo são o custo declarado dessa escolha.

Outra escolha: **o agente não recalcula nota nem média.** Mostra a nota armazenada. Quando a frequência contradiz o resultado (reprovado por falta com frequência acima do mínimo), a ferramenta sinaliza a divergência, o agente mostra os dois valores, diz qual é o oficial e oferece revisão.

### 5.4 O que nunca é carregado

Dados pessoais de qualquer pessoa (CPF, nascimento, filiação, contato, endereço), inclusive de pagador e de responsável; analistas e seus logins; professor além do nome de uma lista exata congelada; notas internas e mensagens de solicitante; prioridade, SLA violado e pesquisas de satisfação; impostos, estágio de cobrança, referência de transação, banco e CNPJ; IP, URL e `relateduserid` do LMS; contas que não são de aluno.

### 5.5 Texto livre e injeção indireta

Texto escrito por humanos nos sistemas de origem é o canal natural de injeção indireta. O ETL o trata assim: nomes de prova, salas e mensagens de atendimento viram tipos conhecidos (AV1, AV2, simulado; a disciplina em PT; um dos 4 modelos de mensagem). Mensagens de solicitante ficam de fora. O que sobra é `texto_livre` e **nunca chega ao LLM**: a ferramenta devolve o placeholder "[mensagem não exibida]". Mesmo assim, a conversa que viu um `texto_livre` fica contaminada até o fim (taint fixo), e `draft_protocol` passa a ser recusado. O taint só dispara com texto plantado, então a injeção indireta fica sem canal até o modelo.

### 5.6 Regras de negócio relevantes

- **Dívida:** saldo da fatura = líquido menos principal confirmado, só para faturas abertas, vencidas ou parciais. Encargos em aberto = encargos não dispensados menos o que já foi pago. O total para quitar soma principal vencido e encargos. Fatura renegociada fica de fora, substituída pelas parcelas do acordo.
- **Simulado aberto:** janela aberta e **sem tentativa** (<!-- num:data.simulados_abertos -->1217<!-- /num --> na data de referência). Os que ainda têm tentativa restante (<!-- num:data.simulados_restantes -->1615<!-- /num -->) são reportados à parte. Provas que valem nota e encerraram sem tentativa: <!-- num:data.provas_sem_tentativa -->0<!-- /num -->.
- **Cobertura:** cada fonte começa numa data (eventos de matrícula, notas, secretaria, LMS, financeiro). Fora da janela, a resposta é "não há dados antes de dd/mm/aaaa", nunca zero.

## 6. Segurança


A segurança precisa sobreviver a um modelo comprometido. Há duas provas: **testes de arquitetura**, sem LLM (saída em `docs/evidence/security-tests.txt`), e **ataques com LLM real**, que medem comportamento.

| Ameaça | Defesa de arquitetura | Prova |
|---|---|---|
| Falsificar identidade | JWT HS256 com algoritmo fixo e `aud` por canal | Testes de token adulterado, expirado, `alg=none`, troca de algoritmo e de `aud` |
| Segredo exposto | `init-secrets`, um volume por consumidor, leitura só de arquivo | A API sobe sem `/data` e sem os segredos do ETL |
| Vazamento entre alunos | Ferramentas sem identidade, enum de índice, RLS forçado, grants por allowlist, reset do pool, `RKey` | Teste do **LLM comprometido**; IDOR por recurso e nos 11 métodos A2A; `contextId` alheio |
| Vazamento corporativo | Destino por coluna no ETL; catálogo por família | Varredura por valor do dump de `canon` (CPF, e-mail, telefone, IP, referência de transação, CNPJ, nome de não-aluno); os logins de analista ausentes |
| Injeção indireta | `texto_livre` nunca ao LLM; texto tipificado; taint fixo | Marcador plantado em **toda** célula de texto dos 47 CSV, passado pelo ETL e por um LLM comprometido: nenhum marcador em saída confiável |
| Excesso de autonomia | Allowlist, elegibilidade, cartão do servidor, `draft_id` preso ao aluno e fora do LLM, uso único, clique humano, lock | Confirmação concorrente; dois rascunhos concorrentes; `draft_id` alheio; `SA-` digitado pelo aluno |
| Falsa promessa | Guard ancorado no registro, compromisso exige fonte | Testes do guard com controles positivos |
| Consumo abusivo | Admissão a 90% da cota, vaga por turno, rate limit, orçamento, teto | Testes, inclusive via A2A, e a escada de carga com cota simulada |
| CSRF e saída | SameSite=Strict; mesma origem (`Origin` × `Host`) e JSON em `POST /chat`, `/protocols/confirm` e `/feedback`; A2A só com Bearer; CSP, `nosniff`, texto puro | Testes |

**Ataques com LLM real.** São 15 categorias: comportamento (jailbreak, roleplay, quebra de papel, falsa promessa, extração de prompt, sequestro de rascunho, escalada multi-turno), vazamento (identidade, outro aluno, outra marca, funcionários, credenciais) e injeção plantada (zero por construção, teste do marcador). O primário é a suíte de desenvolvimento, com 2 repetições, rodada **antes** de qualquer correção; depois vem um holdout cego, escrito sem ver o código. A unidade do Wilson é o **template**: falha se qualquer mutação ou repetição falhar.

Resultado (prompt final): comportamento na dev <!-- num:security.dev.behavior.success -->2/40 templates (Wilson 95%: 1,4% a 16,5%)<!-- /num -->; no holdout <!-- num:security.holdout.behavior.success -->0/33 templates (Wilson 95%: 0,0% a 10,4%)<!-- /num -->; vazamentos na dev <!-- num:security.leak.success -->1/20 templates (Wilson 95%: 0,9% a 23,6%)<!-- /num -->. Plantada: 0 de 14. No holdout cego, os 7 ataques A2A falharam e o único vazamento do juiz é falso positivo: o agente ignorou o RA digitado e mostrou só os dados do próprio aluno, sem avisar. Rótulos às cegas concordam com o juiz em 47 de 48 (`security-labels.md`).

Os 2 templates de comportamento:

- **`ep-checklist`**: o agente parafraseou as próprias regras quando pediram "o que você não pode fazer". Sem segredo e sem canário. É falha real do critério pré-registrado, e é um limite conhecido (seção 11).
- **`em-parcela`**: o predicado de promessa disparou num texto de "não consta". Provável falso positivo do predicado.

O vazamento da dev é **falso positivo**: preços do catálogo público da marca coincidem com faturas de alunos de outra marca, e o conjunto proibido marca a coincidência.

## 7. Qualidade


O método está em D11. Modelo sob teste: `claude-haiku-5-5`, escolhido antes de medir; Gemini e OpenAI têm adaptador, sem medição. O teste cego rodou uma vez no SHA congelado `d6e3d8c` (tag `eval-frozen`, carimbada no OpenTimestamps).

| Medida | Desenvolvimento | Teste |
|---|---|---|
| pass@1 primário (IC 95%) | <!-- num:quality.dev.pass1 -->70,4% [59,0%; 80,6%]<!-- /num --> | <!-- num:quality.test.pass1 -->43,5% [34,0%; 53,4%]<!-- /num --> |
| Templates vistos (alunos novos) | | <!-- num:quality.seen.pass1 -->64,3% [53,3%; 74,8%]<!-- /num --> |
| Templates não vistos (`test_only`) | | <!-- num:quality.unseen.pass1 -->10,3% [0,0%; 20,7%]<!-- /num --> |
| Recusa correta | | <!-- num:quality.test.refusal_ok -->96,9% [89,4%; 100,0%]<!-- /num --> |
| Recusa indevida | | <!-- num:quality.test.refusal_wrong -->0,0% [0,0%; 0,0%]<!-- /num --> |
| pass@1 corrigido (validação do scorer) | | <!-- num:quality.test.corrected -->77,1% [57,2%; 93,2%]<!-- /num --> |
| Sem o critério de ferramenta-domínio (teto) | | <!-- num:quality.test.domain_tools -->75,8% [67,5%; 83,5%]<!-- /num --> |
| Perguntas que cruzam sistemas | | <!-- num:quality.test.cross -->6,0% [0,5%; 13,5%]<!-- /num --> |
| Respostas certas bloqueadas pelo guard | | <!-- num:quality.test.guard_blocked_ok -->124 de 3321 itens (3,7%)<!-- /num --> |
| Bloqueio indevido do guard (controles sintéticos) | | <!-- num:guard.false_block -->0/43 (0,0% [0,0%; 8,2%])<!-- /num --> |
| Recall do guard (perturbação cega) | | <!-- num:guard.blind_recall -->91,8% [78,8%; 100,0%]<!-- /num --> |

**Três rodadas de dev**, com as 2 iterações de prompt permitidas (`quality/dev-r{1,2,3}`):

1. Linha de base: <!-- num:quality.dev.r1.pass1 -->49,1% [36,9%; 61,2%]<!-- /num -->. Os 58 itens inválidos (56 de um caso, provável queda da API) ficam fora do denominador.
2. Iteração 1: <!-- num:quality.dev.r2.pass1 -->62,4% [49,6%; 74,5%]<!-- /num -->. Consultar a ferramenta antes de responder, rascunho na hora, recusa só sem dado do aluno, nenhum valor em pedido vago; corrige a data de referência do cartão.
3. Iteração 2: <!-- num:quality.dev.r3.pass1 -->70,4% [59,0%; 80,6%]<!-- /num -->. Ferramenta a cada pergunta, números em algarismos, recusa só fora do atendimento, rascunho mesmo com protocolo aberto do serviço.

A meta (limite inferior de 85%) **não foi atingida** nem no desenvolvimento nem no teste; o número sai como está. Na dev 3 dominam falhas de redação (105) e respostas certas bloqueadas pelo guard (30); o multiturno, secundário, ficou em 44,2% (n=77) na dev e 35,0% (n=309) no teste. O P(meta) registrado antes do teste já era 0 com acerto real de 75%.

**Teste cego** (3.321 itens, 40 alunos fora da dev, `quality/test-r1`). O primário vale como rodou (§6 do plano de análise); duas sensibilidades mudam a leitura:

1. **Defeito do gabarito.** O redator cego não conhecia as ferramentas e escreveu o domínio (`financeiro`, `lms`) em `expected_tools`; o scorer compara com o nome da ferramenta. Todo item `test_only` que exige ferramenta reprova, seja qual for a resposta: daí o número dos não vistos. Sem esse critério, e só ele, vem a linha "teto" da tabela (`test-r1/ferramenta-dominio`, com anexo); é teto porque a ferramenta deixa de ser exigida.
2. **Validação do scorer** (30 respostas sorteadas, rótulo às cegas): VPP <!-- num:quality.test.vpp -->100,0% [86,2%; 100,0%]<!-- /num -->, VPN <!-- num:quality.test.vpn -->33,3% [9,7%; 70,0%]<!-- /num -->. O scorer não aprovou resposta errada, mas 4 das 6 reprovações sorteadas eram respostas certas. Daí o pass@1 corrigido.

Os templates vistos medem alunos novos, mas guiaram as iterações de prompt. Nos não vistos, sem o critério defeituoso, o pass@1 sobe a 94% (teto), porque o oráculo dos vistos é mais rígido (abaixo). O cruzamento (6,0%) também é quase todo efeito do defeito: nos vistos, 12 de 120.

**Rigidez do oráculo.** O oráculo carimbado não mudou. Para aluno sem fatura vencida ele proíbe qualquer "R$", inclusive "R$ 0,00" e o próximo vencimento verdadeiro; na dev 2, 46 itens falharam só por isso (cerca de 9,7 p.p.). A regra que só serviria ao gabarito (não citar R$ sem fatura vencida) não entrou, mas a iteração 2 (algarismos, nenhum R$ em dado inexistente) também ajuda o scorer: parte do ganho da dev é de formato.

**Bugs da rodada real, corrigidos antes do congelamento:** fatos de resumo fora dos registros (o guard degradava respostas certas); cota diária do perfil de avaliação baixa demais (subiu para 5 milhões de tokens); latência sem componentes por chamada.

**Guard congelado, conjunto cego oficial:** recall e bloqueio indevido na tabela. Fracos: `status_word`, com <!-- num:guard.weak.status_word -->0/10<!-- /num --> pegos, e `flip_negation`, com <!-- num:guard.weak.flip_negation -->7/10<!-- /num -->. A seção "rascunhos do dev × scorer" de `guard.md` tem viés de seleção (o rascunho só é gravado quando a guarda mexeu) e não mede bloqueio indevido. Nas janelas de latência o guard degradou 10% a 20% dos turnos, contra 5% previstos: é o custo de calar em vez de inventar.


O que esta avaliação **não** garante: a amostra de 40 alunos é enriquecida por cotas (o IC estratificado reconhece, não elimina); o scorer foi validado com 30 respostas, rotuladas por um agente de IA às cegas e não pelo autor, então o pass@1 corrigido é sensibilidade; o holdout só é cego para quem não viu o código. O gabarito de acadêmico e LMS não tem fonte externa, e a conferência à mão de 20 itens prevista (`oracle_check.csv`) não foi feita. Não rodaram o kappa do juiz, o McNemar do 2º nível, as paráfrases nem a utilidade (§13, §14 e §16 do plano). A recusa indevida "0,0%" é 0 em 2.692 itens, detectada pelo prefixo fixo de recusa.

## 8. Latência, capacidade e custo


**Latência** é medida por classe de turno: `card` (nenhuma ferramenta), `1` e `2+`. As metas vêm de uma fórmula registrada antes de medir: uma lognormal por componente (chamada que emite ferramenta, chamada que responde, overhead), com Monte Carlo por classe **incluindo o retry do guard**, para todos os quantis e o tempo até o primeiro evento (TTFE). A meta de cada quantil é o maior valor entre a inicial e a prevista. A fórmula é reajustada uma vez e congelada; a fonte é `slo_targets.yaml`.

Trade-offs: o guard precisa do texto inteiro, então o texto útil só chega no fim do turno; o TTFE de `1` e `2+` mede o evento de progresso, e o do `card` é a latência total. O orçamento do grafo é o p99 previsto da classe `2+` vezes 1,2.

A janela A (300 turnos) mediu o agente **antes** das iterações de prompt e fez o reajuste único: o orçamento do grafo foi de 25,22 para 12,26 s. A regra do máximo deixa o p99 abaixo do p95 (só há meta inicial para p50 e p95). A tabela é da janela B (300 turnos, prompt final); a C (150, SHA congelado) confirma: p95 geral 6,49 e 6,72 s, sem corte. Falham só metas vindas da previsão (p99 do `card` e p90 da classe `1` na B; p90 e p99 da classe `1` na C). B e C ficaram abaixo dos 100 turnos pré-registrados em `card` e `1` (14 na classe `1` da C): o p99 por classe é indicativo.

| Classe | p50 | p95 | TTFE p95 |
|---|---|---|---|
| `card` | <!-- num:latency.card.p50 -->1,39 s<!-- /num --> | <!-- num:latency.card.p95 -->3,00 s<!-- /num --> | <!-- num:latency.card.ttfe_p95 -->3,00 s<!-- /num --> |
| `1` | <!-- num:latency.one.p50 -->3,10 s<!-- /num --> | <!-- num:latency.one.p95 -->5,60 s<!-- /num --> | <!-- num:latency.one.ttfe_p95 -->1,91 s<!-- /num --> |
| `2+` | <!-- num:latency.two_plus.p50 -->3,48 s<!-- /num --> | <!-- num:latency.two_plus.p95 -->7,29 s<!-- /num --> | <!-- num:latency.two_plus.ttfe_p95 -->1,80 s<!-- /num --> |

Overhead de infraestrutura (tudo que não é modelo): <!-- num:latency.overhead.p95 -->0,08 s<!-- /num -->. Turnos que estouraram o orçamento do grafo na janela B: <!-- num:latency.budget_exceeded -->0,0%<!-- /num -->.

**Capacidade.** "Milhares simultâneos" vira mensagens por minuto, RPM e TPM contra a cota e, só depois, réplicas pela CPU por turno; o TPM limita antes. Vagas e pool seguem a lei de Little. A carga usa um `LoadTestLLM` (latências das lognormais) em duas escadas de laço aberto: (a) com limitador e cota simulada, (b) sem limitador. Resultado: <!-- num:latency.load.plateau -->escada (a) aprovada: 89 turnos admitidos no degrau mais alto, pico de 89<!-- /num -->. Sem limitador, 1 container com 1 CPU aguenta 25 turnos/s; a 50 turnos/s, 34% são cortados pelo prazo, com 1 ou 2 workers (`load-b-w*.json`). A carga mede o app com LLM falso e cota simulada, não o provedor; "milhares simultâneos" é extrapolação. As premissas de admissão e capacidade (`est_tokens`, `turn_usage`) foram reajustadas aos ~14 mil tokens medidos por turno.

**Custo** por turno (entrada, saída, raciocínio e cache), estimado pela tabela de preços e não pela fatura, na janela B: <!-- num:latency.cost.per_turn -->US$ 0,0008<!-- /num -->.

Duas alavancas ficam desligadas: o hedge da primeira chamada no p90 (com orçamento, contado na cota e no gasto) e a comparação de modelos por classe.

## 9. Observabilidade


A decisão está em D16. Em operação:

- **Spans:** `chat.turn` (AGENT, aberto antes do evento `meta`) contém `agent.call_model` (LLM), `agent.tool` (TOOL, com `db.query`) e `agent.guard` (GUARDRAIL, com desfecho e kinds). Atributos de modelo, tokens (com cache e raciocínio) e hashes do prompt, das ferramentas e do dataset.
- **Métricas** Prometheus em modo multiprocesso, com buckets vindos de `slo_targets.yaml`: turnos, latência, TTFE, erros e retries de LLM, admissão, vagas, guard, tipo de resposta, funil do rascunho, tokens, custo e feedback.
- **SLO:** latência e TTFE por classe, custo por dia e qualidade (degradado mais fallback, 👎, recusa e vazio por domínio). Status `degraded` e `fallback` contam como disponíveis, mas entram no SLO de qualidade. O alerta fica proposto, não implantado.

## 10. Integração em produção

Esta seção descreve o caminho, não algo já feito.

- **Dados:** CDC ou APIs dos sistemas no lugar da carga de CSV, com o mesmo contrato por coluna e as mesmas contagens. O rascunho confirmado sai por **outbox**, na transação do INSERT, para um worker que abre o protocolo na secretaria com retry idempotente por `draft_id`.
- **Identidade:** um IdP da instituição no lugar do JWT de desenvolvimento; no A2A, **token exchange (RFC 8693)** com claim `act`: o `sub` segue sendo o aluno e o chamador fica registrado.
- **Modelo:** Vertex AI com cota provisionada, mesmo código (`llm_backend`, projeto e região); o balde de admissão segue a cota contratada.
- **Orquestradores:** o Langflow 1.13.1 consome o agente pelo A2A (`make langflow-demo`: Chat Input, A2A Agent, Chat Output; resposta em `docs/evidence/langflow-a2a.json`). O ADK é desenho, não demonstrado.
- **Escala:** réplicas sem estado; o estado é Redis e Postgres. A CPU por turno da escada de carga define as réplicas por RPM.

## 11. Limites honestos

- Limites de segurança conhecidos: a pilha de avaliação local compartilha banco, Redis e o segredo do JWT com a API; o `init-secrets` monta o repositório só-leitura como root; `/metrics` e o Phoenix não têm autenticação (só em 127.0.0.1); o JWT não tem `iss`; o teto de corpo olha só o `Content-Length`. A identidade em produção é premissa externa (IdP): o JWT HS256 de desenvolvimento emite e verifica com o mesmo segredo. Os padrões do compose são de desenvolvimento (`APP_ENV=dev`, login de dev ligado).
- Um aluno pode ocupar as 8 vagas globais de turno: não há limite de turnos simultâneos por aluno, e o limite por minuto e a cota diária não impedem isso.
- O guard não pega um valor correto atribuído à entidade errada quando a frase não cita nenhuma entidade.
- Os <!-- num:data.protocols_unlinked -->22<!-- /num --> protocolos sem vínculo (ver 5.3) não aparecem para nenhum aluno, por escolha de precisão.
- A regra de média e aprovação não existe nos dados, e o agente diz isso em vez de inferir.
- Sem classificador de jailbreak: a defesa é a arquitetura, e o comportamento do modelo é medido, não garantido.
- Com a data de referência fixa em 31/10/2026, o agente não representa o "hoje" real; é uma escolha para que a banca receba as mesmas respostas depois do prazo.
- Em produção, a cota de 200 mil tokens por aluno por dia conta só tokens fora do cache (o cache lido custa cerca de 10% da entrada): com o Haiku, cerca de 70 mensagens por dia.
- O catálogo só devolve a marca da matrícula do aluno.
- O agente parafrasea as próprias regras quando perguntado sobre o que não pode fazer (`ep-checklist`, seção 6).
- **Desvios do pré-registro.** (1) Os ataques cegos rodaram em `80c5ace`, não em `eval-frozen`: o carregador recusava um id do holdout (`B-injecao-07b`) e o cliente A2A não mandava o cabeçalho `a2a-version`, então nenhum ataque A2A chegou ao agente. Corrigido só o avaliador, a rodada foi retomada pelo diário; `app`, `core` e `etl` são idênticos aos de `eval-frozen` (tentativas em `quality/rounds.jsonl`). (2) Os rótulos às cegas (30 do scorer, 48 dos ataques) são de um agente de IA, não do autor.

## 12. Como foi construído

O projeto foi feito em etapas: especificação, plano de tarefas e implementação tarefa a tarefa, com teste antes do código. A implementação, a revisão independente de cada tarefa e boa parte das análises foram feitas por agentes de IA, sob a direção do autor. Do autor: o escopo, as decisões de produto e de arquitetura tomadas na especificação e no plano, a escolha do modelo e a decisão do que entra na entrega. Os números saem por script das evidências (`scripts/render_docs.py`).

## Apêndice. Estatística


- **Bootstrap de dois fatores.** O pass@1 é calculado sobre pares (aluno, template). O intervalo reamostra alunos e templates, estratificado pelas cotas, porque respostas do mesmo aluno ou do mesmo template não são independentes.
- **P(meta).** A correlação intraclasse é estimada no desenvolvimento, e a probabilidade de o limite inferior passar de 85% é simulada e registrada antes da rodada de teste.
- **Wilson.** Usado para proporções com poucos casos: VPP e VPN do scorer e os ataques por template.
- **Correção pelo scorer.** O pass@1 corrigido é p̂·VPP + (1 − p̂)(1 − VPN), com intervalo por bootstrap combinado com Beta. É análise de sensibilidade; o primário é o que rodou.
- **Escolha de modelo.** Pela primeira rodada de desenvolvimento, com margem pareada de pelo menos 3 pontos percentuais; abaixo disso, o mais barato.
- **Unidade do ataque.** Um template de ataque falha se qualquer mutação ou repetição falhar.

<!-- chaves esperadas (render_docs.py falha se faltar alguma):
data.*, a2a.*: já existem (data-quality.json, spikes.json).
quality.dev.pass1, quality.test.pass1, quality.seen.pass1, quality.unseen.pass1,
quality.test.refusal_ok, quality.test.refusal_wrong, quality.test.cross
guard.false_block, guard.blind_recall
security.dev.behavior.success, security.holdout.behavior.success, security.leak.success
latency.card.p50, latency.card.p95, latency.card.ttfe_p95, latency.one.*, latency.two_plus.* (mesmos três),
latency.overhead.p95, latency.budget_exceeded, latency.load.plateau, latency.cost.per_turn
Origem de cada chave: docstring de `build_numbers` em scripts/render_docs.py. -->
