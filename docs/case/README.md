# Case — Engenharia de IA: agente de atendimento ao aluno

## Contexto

Um grupo educacional fictício opera **5 marcas** de ensino superior (UniSaoPaulo, UniMontanhas, UniLitoral, UniAtlantico e UniAraucaria), nas modalidades **presencial** e **EAD**. Os dados de cerca de 1.000 alunos estão espalhados por **quatro sistemas** independentes, cada um com convenções próprias de nomes, formatos e identificadores.

O arquivo [`dados.zip`](dados.zip) traz as 47 tabelas desses sistemas em CSV (UTF-8, com cabeçalho), organizadas em uma pasta por sistema. Os dados são sintéticos, mas reproduzem situações de sistemas reais, inclusive inconsistências. A data de referência é **31/10/2026**: o semestre 2026/2 está em andamento.

---

## 1. O desafio

Construir um **agente de IA para atender alunos**. Ele deve, por exemplo:

- **Tirar dúvidas** sobre curso, matriz curricular, calendário, disciplinas e serviços da secretaria.
- **Consultar a vida acadêmica** do aluno: status da matrícula, notas, frequência, disciplinas cursadas, progresso no curso.
- **Consultar a situação financeira** do aluno: mensalidades, pagamentos, débitos e acordos.
- **Resolver protocolos:**
  - consultar o andamento dos protocolos do aluno;
  - orientar sobre os serviços disponíveis;
  - abrir novas solicitações quando fizer sentido.
- **Consultar a atividade no ambiente virtual (LMS):** provas, tentativas, acessos.

### Requisitos obrigatórios

1. **Escalável:**
   - pensado para atender milhares de alunos simultâneos;
   - mantém latência baixa e custo controlado.
2. **Seguro:**
   - zero risco de **jailbreak**, **prompt injection** e outras técnicas de ataque a LLMs ou aos sistemas por trás deles;
   - zero vazamento de dados **entre usuários**: um aluno nunca acessa dados de outro aluno;
   - zero vazamento de **dados corporativos**: dados internos, de funcionários, de outras marcas, prompts, credenciais etc.
3. **Correto:** respostas fiéis aos dados. Quando a informação não existir ou estiver fora do escopo, o agente deve dizer isso, e não inventar.

### Premissa de identidade

O agente atende um aluno **já autenticado** pela aplicação. Você pode simular o login como preferir, desde que a identidade do aluno venha da sessão e **nunca** do texto da conversa.

---

## 2. Liberdade e critérios de avaliação

Você tem liberdade total de tecnologia:
- qualquer linguagem, modelo (comercial ou aberto) e framework de agentes;
- ou nenhum framework.

A **estratégia e a arquitetura** fazem parte da avaliação. Técnicas como vetorização (RAG), grafos de conhecimento e ontologias, controle de acesso e validação de consultas impactam diretamente o resultado.

| Critério | O que observamos |
|---|---|
| **Qualidade e assertividade das respostas** | Respostas corretas, completas e coerentes com os dados, inclusive nas perguntas que exigem cruzar sistemas; recusa adequada quando não há resposta |
| **Latência** | Tempo de resposta das perguntas, medido e relatado por você |
| **Segurança** | Resistência a jailbreak, prompt injection, extração de dados de terceiros e de dados corporativos. Quanto mais a segurança depender de garantias de arquitetura, e não só de instruções no prompt, melhor |
| **Arquitetura e engenharia** | Clareza das decisões, escalabilidade, organização do código, testes e observabilidade |

### Entregáveis

1. **Repositório com o código** e instruções para rodar.
2. **Documento de arquitetura:** decisões, trade-offs e por que você escolheu cada técnica.
3. **Evidências:**
   - exemplos de conversas;
   - testes de segurança (tentativas de ataque e o resultado);
   - medição de latência;
   - como você avaliou a qualidade das respostas.

---

## 3. Os sistemas e suas tabelas

Cada sistema tem as próprias convenções de nomes de colunas, de datas e horários e de valores. Os sistemas **se referem às mesmas entidades** (alunos, marcas, cursos, unidades, protocolos) usando **identificadores e formatos diferentes**, e descobrir como relacioná-los faz parte do desafio. Abaixo, para cada tabela, indicamos apenas a chave primária (**PK**) e as chaves estrangeiras (**FK**) dentro do próprio sistema.

### 3.1 SAG — sistema acadêmico (`sag/`)

O sistema oficial da vida acadêmica: matrículas, cursos, matrizes, turmas e notas. Colunas em MAIÚSCULAS; notas de 0 a 10.

| Tabela | Descrição | Linhas | PK | FKs |
|---|---|---|---|---|
| `brand` | Marcas (cada marca separada em presencial e EAD) | 10 | BRAND | — |
| `unit` | Unidades: campi e polos EAD | 27 | UNIT_ID | BRAND → brand |
| `course` | Cursos de graduação | 8 | COURSE | — |
| `curriculum` | Matrizes curriculares (versões de cada curso) | 10 | CURRICULUM_ID | COURSE → course |
| `subject` | Catálogo de disciplinas | 260 | SUBJECT_CODE | — |
| `curriculum_subject` | Disciplinas de cada matriz, por semestre | 328 | CURRICULUM_ID, SUBJECT_CODE | CURRICULUM_ID → curriculum; SUBJECT_CODE → subject; PREREQUISITE_CODE → subject |
| `course_offer` | Cursos oferecidos em cada unidade e turno | 138 | OFFER_ID | UNIT_ID → unit; COURSE → course; CURRICULUM_ID → curriculum |
| `term` | Calendário acadêmico (semestres) | 12 | TERM | — |
| `professor` | Professores | 60 | PROFESSOR_ID | BRAND → brand |
| `person` | Dados pessoais dos alunos | 1.015 | PERSON_ID | — |
| `enrollment` | Matrículas: uma por aluno em cada curso | 1.037 | BRAND, STUDENT_ID | BRAND → brand; PERSON_ID → person; COURSE → course; CURRICULUM_ID → curriculum; UNIT_ID → unit; ENTRY_TERM → term; (ORIGIN_BRAND, ORIGIN_STUDENT_ID) → enrollment |
| `enrollment_status_history` | Histórico de mudanças de status da matrícula | 1.559 | HISTORY_ID | (BRAND, STUDENT_ID) → enrollment; TERM → term |
| `term_enrollment` | Rematrícula a cada semestre | 4.637 | BRAND, STUDENT_ID, TERM | (BRAND, STUDENT_ID) → enrollment; TERM → term |
| `class_section` | Turmas de cada disciplina por semestre | 11.051 | CLASS_ID | TERM → term; SUBJECT_CODE → subject; BRAND → brand; UNIT_ID → unit; PROFESSOR_ID → professor |
| `student_class` | Disciplinas cursadas, com notas, frequência e resultado | 20.277 | CLASS_ID, BRAND, STUDENT_ID | CLASS_ID → class_section; (BRAND, STUDENT_ID) → enrollment; CURRICULUM_ID → curriculum |

### 3.2 LMS — ambiente virtual de aprendizagem (`lms/`)

Uma instância no estilo Moodle compartilhada pelas marcas, com dados a partir de 15/01/2023. Horários em epoch (segundos, UTC). Atenção: no LMS, `mdl_course` é uma **sala de aula** (turma de uma disciplina), não um curso de graduação.

| Tabela | Descrição | Linhas | PK | FKs |
|---|---|---|---|---|
| `mdl_tenant` | Ambientes (um por marca) | 5 | id | — |
| `mdl_course_categories` | Árvore de categorias: ambiente > modalidade > curso | 75 | id | tenant_id → mdl_tenant; parent → mdl_course_categories |
| `mdl_user` | Contas: alunos, professores, administradores e testes | 1.078 | student_lms_id | tenant_id → mdl_tenant |
| `mdl_user_info_field` | Campos de perfil personalizados | 4 | id | — |
| `mdl_user_info_data` | Valores dos campos de perfil | 2.957 | id | userid → mdl_user; fieldid → mdl_user_info_field |
| `mdl_course` | Salas de aula virtuais | 9.214 | id | tenant_id → mdl_tenant; category → mdl_course_categories |
| `mdl_user_enrolments` | Usuários inscritos em cada sala, com papel | 30.442 | id | courseid → mdl_course; userid → mdl_user |
| `mdl_course_modules` | Atividades e materiais (páginas, fóruns, provas…) | 58.390 | id | course → mdl_course |
| `mdl_quiz` | Provas online | 12.360 | id | course → mdl_course |
| `mdl_quiz_attempts` | Tentativas de prova | 18.857 | id | quiz → mdl_quiz; userid → mdl_user |
| `mdl_logstore_standard_log` | Log de eventos (acessos, visualizações, envios) | 151.111 | id | userid → mdl_user; courseid → mdl_course; relateduserid → mdl_user |

### 3.3 Secretaria — protocolos de atendimento (`secretary/`)

O sistema de atendimento ao aluno, com dados a partir de 01/03/2022. Horários em texto ISO-8601 (UTC).

| Tabela | Descrição | Linhas | PK | FKs |
|---|---|---|---|---|
| `unit` | Unidades de atendimento | 27 | Unit_Code | — |
| `analyst` | Analistas de atendimento | 25 | Analyst_Login | Unit_Code → unit |
| `demand_type` | Catálogo de serviços, com SLA e taxa | 18 | Demand_Code | — |
| `protocol` | Protocolos abertos pelos alunos ou responsáveis | 3.933 | Protocol | Unit_Code → unit; Demand → demand_type; Demand_Analyst → analyst |
| `protocol_status_history` | Mudanças de status de cada protocolo | 14.274 | Protocol, Seq | Protocol → protocol |
| `protocol_interaction` | Mensagens trocadas em cada protocolo | 14.853 | Interaction_ID | Protocol → protocol |
| `issued_document` | Documentos emitidos (certificados, históricos, diplomas) | 1.001 | Document_ID | Protocol → protocol; Signed_By → analyst |
| `satisfaction_survey` | Pesquisas de satisfação | 1.503 | Survey_ID | Protocol → protocol |

### 3.4 Financeiro — cobrança e recebíveis (`finance/`)

O sistema de cobrança. Ele conhece o **pagador**, que pode ser o próprio aluno, um responsável ou uma empresa. Valores em **centavos**; taxas entre 0 e 1.

| Tabela | Descrição | Linhas | PK | FKs |
|---|---|---|---|---|
| `company` | Empresas (uma por marca) | 5 | companyCode | — |
| `product` | Produtos: mensalidades por curso e modalidade, e serviços | 15 | productCode | — |
| `price_list` | Preços por produto, empresa e ano | 450 | priceId | productCode → product; companyCode → company |
| `customer` | Pagadores | 985 | customerId | — |
| `contract` | Contratos financeiros dos alunos | 1.065 | contractId | customerId → customer; companyCode → company; productCode → product |
| `discount` | Bolsas e descontos | 304 | discountId | contractId → contract |
| `invoice` | Faturas: mensalidades, taxas de serviço e parcelas de acordo | 25.931 | invoiceId | contractId → contract; customerId → customer; companyCode → company; productCode → product; agreementId → agreement |
| `invoice_tax` | Impostos de cada fatura | 76.692 | invoiceId, taxType | invoiceId → invoice |
| `payment` | Histórico de pagamentos | 24.380 | paymentId | invoiceId → invoice |
| `late_charge` | Multas e juros por atraso | 5.241 | lateChargeId | invoiceId → invoice |
| `receivable_snapshot` | Posição mensal da dívida por contrato | 6.866 | snapshotDate, contractId | contractId → contract; customerId → customer; companyCode → company |
| `agreement` | Acordos de renegociação de dívida | 65 | agreementId | contractId → contract; customerId → customer |
| `agreement_invoice` | Faturas incluídas em cada acordo | 196 | agreementId, invoiceId | agreementId → agreement; invoiceId → invoice |

---

Boa sorte! Dúvidas sobre o case podem ser enviadas ao recrutador responsável.
