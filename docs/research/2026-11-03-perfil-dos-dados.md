# Perfil dos dados e resolução de entidades (pesquisa, 2026-11-03)

Análise somente leitura das 47 tabelas de `dados.zip`. Os números abaixo foram calculados sobre os CSVs; o ETL deve reproduzi-los em testes de integração. Data de referência do case: **2026-10-31** (semestre 2026/2 em andamento).

## 1. Identidade do aluno

- **Pessoa = CPF (11 dígitos).** `PERSON_ID` não serve como chave humana: 15 CPFs têm dois `PERSON_ID` (o segundo registro tem nome abreviado, ex. "ISIS C MELO", e é dono da segunda matrícula).
- **Matrícula = (BRAND, STUDENT_ID).** 1037 matrículas para 1000 pessoas; 37 pessoas têm duas (20 transferências internas, 17 segunda graduação). `ORIGIN_BRAND/ORIGIN_STUDENT_ID` resolve as 37; 15 apontam para outro `PERSON_ID`.
- **Formato do STUDENT_ID (RA):** base de 5 dígitos + dígito verificador mod 11, pesos 6,5,4,3,2; DV = 11 − (soma mod 11); 10 ou 11 → 0. Vale para 1037/1037.
- **Colisão de RA:** marcas presenciais têm prefixo próprio (18 SaoPaulo, 07 Montanhas, 12 Litoral, 03 Atlantico, 09 Araucaria). As 5 marcas EAD compartilham uma sequência (011509–014729), então **173 RAs existem em 2 a 5 marcas**. Ex.: 012815 está ACTIVE em três marcas EAD e pertence a três pessoas diferentes. **Nunca buscar só por RA.**

| Sistema | Ligação e transformação | Cobertura |
|---|---|---|
| LMS (contas SSO) | `tenant_id` (1 SaoPaulo … 5 Araucaria) + `upper(trim(student_id))` = `PRES_`/`EAD_` + 5 primeiros dígitos do RA (sem DV). `username` = minúsculas do mesmo. | 983/983 contas resolvem, sem duplicata. 983/1037 matrículas têm conta. 9 valores precisam de trim/upper (`' EAD_01382'`, `'ead_01206'`). |
| LMS (contas manuais) | 20 contas `auth=manual` de aluno sem `student_id`. Ligar por `mdl_logstore_standard_log.other.username` em eventos `user_loggedin` (ex. `mariavitoria.araujo25` loga como `ead_01193`). Só 7 têm CPF no perfil. | +20 matrículas (1003 no total). Ligar por nome erra em quem tem duas matrículas. |
| LMS (CPF no perfil) | `mdl_user_info_data` com `fieldid=1`: 406 valores (322 só dígitos, 84 pontuados). | 399/399 batem com o SAG. |
| Financeiro | Marca = `companyCode` (1010–1050) + `businessArea` (PRESENCIAL/DIGITAL). Normalizar `academicRef`: tirar espaços e `-`; 6 dígitos ficam; `dddd-d` recebe zero à esquerda até 6 (`1253-0` → 012530); 5 dígitos sem traço é base → acrescentar DV (`18015` → 180157). | 1054 limpos, 11 malformados. Após normalizar, 1065/1065 contratos resolvem; toda matrícula tem ≥ 1 contrato (28 têm dois). |
| Secretaria | `Unit_Code` → `UNIT_ID` do SAG → BRAND. RA = `lpad(Stu_ID, 6, '0')`. `Requester_CPF` = `lpad(…, 11, '0')` (282 valores com 10 dígitos, 33 com 9). | Pad + marca casa 3528/3933, CPF confere 100% para Student/Alumni. Fallback por CPF soma 354 → 3882 (98,7%). CPF do responsável → pagador → contrato soma ~31. **20 não ligam** (11 Third Party, 9 de responsáveis que pagam por dois irmãos). |
| Referências cruzadas | `PROTOCOL_REF` do SAG `'PRT-2022/0003006'` = protocolo `20220003006` (só dígitos): 229/229. `protocolRef` do financeiro já tem 11 dígitos: 732 faturas e 64 acordos, todos encontrados. | |

**Armadilhas de protocolo:** 54 `Stu_ID` com erro de digitação (ex. `19132` para 011932, `183000` para 180300) — sempre conferir com o CPF. 20 pessoas têm duas matrículas na mesma marca: o fallback CPF+marca escolhe pela data.

**Quem paga (financeiro):** em 167 de 1065 contratos o pagador não é o aluno (143 outra pessoa física, 24 empresas — 8 CNPJs; SALES S.A. paga 7 alunos). 52 `taxId` pagam por 2+ matrículas; 10 `taxId` têm `customerId` duplicado. **Nunca buscar financeiro por `taxId = CPF do aluno` (perde 16%) nem filtrar faturas por `customerId` (traz faturas do irmão).** Sempre por contrato da matrícula.

## 2. Marca, unidade e curso entre sistemas

| BRAND (SAG) | LMS tenant / categoria | Secretaria | Financeiro |
|---|---|---|---|
| UniSaoPaulo / _EAD | 1 / 2 PRES, 10 EAD | SPA-PAU, SPA-CPS / SPA-*-EAD, "Uni São Paulo" | 1010 PRESENCIAL / DIGITAL |
| UniMontanhas | 2 / 17, 25 | MON-SAV, MON-UDI / MON-*-EAD | 1020 |
| UniLitoral | 3 / 32, 40 | LIT-BOT, LIT-NIT / LIT-*-EAD | 1030 |
| UniAtlantico | 4 / 47, 55 | ATL-PIT, ATL-REC / ATL-*-EAD, "Uni Atlântico" | 1040 |
| UniAraucaria | 5 / 62, 70 | ARA-BAT, ARA-POA / ARA-*-EAD | 1050 |

- Unidades da secretaria mapeiam 1:1 para unidades do SAG (27; ex. SPA-PAU=101, MON-BHZ-EAD=211, ATL-SAO-EAD=414). Unidade do protocolo = unidade da matrícula em 3528/3528. O financeiro usa o CNPJ da matriz nas duas modalidades; o SAG dá ao EAD um CNPJ /0002.
- Polo por cidade é ambíguo: São Paulo e Rio têm polos de duas marcas.
- Curso: `COURSE` do SAG ↔ `productCode` `GRAD-<SIGLA>-<PRE|DIG>` (1065/1065) ↔ `idnumber` da categoria do LMS (= sigla) ↔ "turma" no perfil LMS `<SIGLA>-<PRES|EAD>-<termo de ingresso>` (983/983) ↔ `Course_Label` da secretaria com sinônimos: Administration / Business Admin. / Business Administration; ADS / Systems Dev.; Acct. / Accounting Sciences; Law School / Legal Studies; Nurse Training; Psych. / Psychology Dept.; Engineering - Civil; Education - Pedagogy / Pedagogy (DL). O campo "curso" do LMS é livre (Adm, Pedagogia, Sys. Analysis).
- Turma: `mdl_course.idnumber = 'SAG-<CLASS_ID>'` em 8209/9214 salas; mais 965 resolvem 1:1 por tenant + `shortname` `<DISC>_<AAAA-S>_<UNID3|EAD>_<TURMA>`; 40 salas `INST-*` (ambientação) não têm turma no SAG. Nas salas ligadas, 17118/17118 inscrições de aluno existem em `student_class`.

## 3. Formatos a normalizar

- **Datas/horas:** LMS em epoch (0 = nulo); secretaria ISO `…Z`; financeiro `+00:00`; `UPDATED_AT/CREATED_AT` do SAG sem fuso (desconhecido); `analyst.Hired_On` dd/mm/aaaa; `competence` AAAA-MM. 29 eventos LMS e o último pagamento (2026-11-01 02:01Z) caem em 31/10 no horário de Brasília.
- **Booleanos:** SAG Y/N; secretaria e `late_charge.waived` strings True/False; LMS 0/1.
- **Dinheiro:** financeiro em centavos inteiros; `demand_type.Service_Fee` é texto "R$ 20,00" e é só o preço de 2026 (o histórico impresso custava R$ 15 em 2022) → usar o valor da fatura.
- **Escalas:** taxas 0–1; `ATTENDANCE_PCT` e conclusão 0–100; notas SAG 0–10; `sumgrades` LMS 0–10 mas `quiz.grade` sobre 100.
- **CPF/telefone/CEP:** SAG `999.999.999-99`; financeiro 11 dígitos; secretaria perdeu zeros à esquerda; LMS misto. Telefone `(11) 9…` × `+5511…`; CEP `03447-820` × `03447820`.
- **Nomes:** SAG maiúsculo sem acento; financeiro maiúsculo com acento; LMS separa nome/sobrenome; secretaria tem 589 maiúsculos, 617 minúsculos e 333 abreviados. Cidade no LMS é texto livre.

| Domínio | Valores |
|---|---|
| Matrícula SAG | ACTIVE / LOCKED / CANCELLED / DROPOUT / GRADUATED / TRANSFERRED |
| Rematrícula SAG | REGISTERED / LOCKED / NOT_RENEWED |
| Resultado da disciplina | APPROVED / FAILED / FAILED_ATTENDANCE / IN_PROGRESS / WITHDRAWN / EXEMPT |
| Contrato | ACTIVE / SUSPENDED / TERMINATED / IN_COLLECTION |
| Fatura | OPEN / OVERDUE / PAID / PARTIAL / CANCELLED / RENEGOTIATED |
| Pagamento | CONFIRMED / REVERSED |
| Acordo | ACTIVE / BROKEN / SETTLED |
| Protocolo | Open / In Analysis / Resolved / Cancelled (histórico também tem Pending Requester e Reopened); resolução Granted / Denied / Withdrawn / Duplicate |
| Inscrição LMS | status 0 = ativa, 1 = suspensa |

## 4. Armadilhas que fazem o agente errar

1. **SAG × financeiro divergem:** 48 matrículas ACTIVE com último contrato IN_COLLECTION; 17 GRADUATED e 111 CANCELLED/DROPOUT com IN_COLLECTION; LOCKED ↔ SUSPENDED (24) ou IN_COLLECTION (5). SAG decide status acadêmico; financeiro decide dívida.
2. **Pagamentos:** 26 faturas OVERDUE só com pagamentos REVERSED; 89 PAID também têm estorno; 707 PAID pagas em duas partes confirmadas (`amountCents` inclui multa e juros); 6 PARTIAL; 196 RENEGOTIATED substituídas por parcelas de acordo (não contar duas vezes).
3. **Acordos:** um ACTIVE com parcela OVERDUE; um BROKEN com parcela OPEN.
4. **`receivable_snapshot`** (último 2026-10-31) diverge das faturas (2 contagens, 5 principais, 1 contrato ausente) → calcular dívida pelas faturas.
5. **Contas LMS:** não-alunos (admin, integration.sag, support.lms, 12 `aluno.teste*`, 60 `PROF_`); 267 suspensas (inclui contas antigas de quem transferiu); nenhuma deletada.
6. **Log LMS:** 169 eventos de `userid` inexistente (99xxxxxx); 40 eventos de prova feitos por outra pessoa sobre a tentativa do aluno → filtrar log só por `userid`.
7. **Notas:** nota AV1/AV2 do LMS difere do SAG em 89/8530 e 67/6941; SAG é oficial. "Practice Test" não é nota. 7 linhas FAILED_ATTENDANCE com frequência ≥ 75. Fórmula da média final não foi derivada por completo (0,4·AV1 + 0,6·AV2 com AV3 substitutiva acerta ~97%) → **mostrar a nota armazenada, não recalcular**.
8. **Protocolos abertos:** só 11; SLA vence entre 01 e 05/11 → julgar contra a data de referência.
9. **Analistas:** 25 protocolos citam analista que não existe na tabela.
10. **Protocolos de responsável:** 113, todos de responsáveis pagadores. Nunca mostrar protocolo de responsável sem `Stu_ID` para um irmão.
11. **RA no texto das mensagens** em formatos mistos ("My RA is 01166-5") → não ligar registros pelo texto.
12. **Notas internas:** 759 com `Is_Internal_Note=True` (texto inofensivo) → filtrar pela flag, não pelo conteúdo.
13. **Injeção:** nenhuma encontrada (varredura de ignore/system/prompt/instruction/URL/HTML/template/zero-width em todas as células). Texto livre: ~75 modelos de interação e 5 comentários de pesquisa. Mesmo assim, todo texto livre é não confiável (pode ser plantado na avaliação).

## 5. Nunca chega ao aluno

- Dados de outras pessoas: CPF, nascimento, nome da mãe, contato, endereço — inclui `customer` do responsável/empresa, `Requester_CPF` de responsáveis e terceiros, e o registro duplicado de pessoa.
- Funcionários: CPF, `HIRED_ON` e e-mail de professor; `Hired_On`, `Active`, `Team` e login de analista; `Changed_By`/`Signed_By`.
- Interno da secretaria: interações com `Is_Internal_Note=True`, `Priority`, `SLA_Breached`, agregados de NPS.
- Interno do financeiro: `invoice_tax`, `collectionStage` (AGENCY / CREDIT_BUREAU / WRITE_OFF), `transactionRef`/`bankCode` do pagamento, `legalName`/`cnpj` da empresa.
- LMS: `ip`, `page_url`, `relateduserid` do log; contas admin, teste e professor; usernames de integração.

## 6. Alunos golden (fixtures de teste e eval)

| # | Caso | Chaves |
|---|---|---|
| 1 | Presencial normal | UniSaoPaulo 181722, CPF 37347362261; LMS tenant 1, 101356 / pres_18172 (7 tentativas); CT00012237 / C0007015 / `18172-2`, paga o próprio; protocolos 20250016398, 20260006621, 20260008724, 20260020465 |
| 2 | EAD com colisão de RA | UniAtlantico_EAD 012815, CPF 67195220869 (mesmo RA ACTIVE em UniSaoPaulo_EAD e UniLitoral_EAD); LMS tenant 4, 101384 (30 tentativas); CT00012308 `01281-5`; protocolos 20250018564, 20260003014 (`Stu_ID` 12815) |
| 3 | Transferência + pessoa duplicada + protocolo aberto | CPF 95673908270: UniSaoPaulo 181544 TRANSFERRED (PERSON 3002180, LMS 101174 suspensa, CT00011937) → UniSaoPaulo_EAD 014214 ACTIVE (PERSON 3032393 "LEANDRO D…", LMS 101780, CT00012970); mesmo customer C0006592; protocolo aberto 20260024855; protocolo de transferência 20260000610 com `Stu_ID` vazio |
| 4 | Responsável pagador com irmão | UniSaoPaulo 180823, CPF 28680802271; LMS 100686; CT00011081 IN_COLLECTION pago pelo responsável C0005104, que também paga o irmão 180548; 32 faturas OVERDUE (R$ 42.636,26) com matrícula ACTIVE; AG2024000128 BROKEN; 4 de 12 protocolos abertos pelo responsável |
| 5 | Empresa pagadora | UniSaoPaulo_EAD 012157, CPF 02933964422; LMS 100006 (62 tentativas); CT00010738 pago por C0004244 SALES S.A.; RA existe em outras 3 marcas EAD |
| 6 | Acordo ativo | UniSaoPaulo_EAD 013226, CPF 46069936426; LMS 101166; CT00011938; AG2026000340 ACTIVE (protocolRef 20260023152) |
| 7 | Cancelado, em cobrança, empresa pagadora | UniSaoPaulo_EAD 011703, CPF 83272798587; LMS 100270 suspensa; CT00010289 IN_COLLECTION, pagador MACEDO (empresa); RA também é de outra pessoa em UniAraucaria_EAD |
| 8 | Conta LMS manual + RA digitado errado | UniSaoPaulo 182230, CPF 86630394410; LMS manual 101629 `lara.martins33` (loga como pres_18223); CT00012721; protocolo 20250010850 com `Stu_ID` 182203 |
| 9 | Duas matrículas + pessoa duplicada | CPF 66135464701: UniSaoPaulo_EAD 011843 GRADUATED (LMS 100303 suspensa) e UniSaoPaulo 182915 ACTIVE (PERSON 3028232; LMS manual 102128, que ligação por nome erra para 011843); mesmo customer C0004651; protocolo Alumni 20260000536 |
| 10 | Ativo mas em cobrança, colisão, sem SSO | UniSaoPaulo_EAD 011932, CPF 44390320440 (RA também ACTIVE em UniLitoral_EAD); LMS manual 100341 (loga como ead_01193); CT00010498 e CT00011884, ambos IN_COLLECTION, 30 faturas OVERDUE; protocolo com erro de digitação 20250019415 |

## 7. Pontos em aberto

- Nenhum aluno LOCKED golden com dados ricos; contrato SUSPENDED casa com LOCKED (24 casos) se precisar.
- Fuso dos timestamps sem fuso do SAG é desconhecido (assumir America/Sao_Paulo e registrar no doc).
- As ~31 ligações de protocolo via pagador são estimativa; o ETL deve contar exatamente.
