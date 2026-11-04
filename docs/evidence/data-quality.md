# Qualidade dos dados (carga)

Gerado pelo ETL na própria carga. Nenhum CPF, e-mail, telefone ou nome aparece aqui.

## Igualdade das contagens SQL com as contagens em memória

Medidas dentro da transação, antes de `rls.sql` (depois do FORCE o dono lê 0 linhas).

| tabela | memória | SQL | igual |
| --- | ---: | ---: | --- |
| agreement | 65 | 65 | ok |
| agreement_invoice | 196 | 196 | ok |
| brand | 10 | 10 | ok |
| class_result | 20277 | 20277 | ok |
| contract | 1065 | 1065 | ok |
| course | 8 | 8 | ok |
| course_offer | 138 | 138 | ok |
| coverage | 6 | 6 | ok |
| curriculum | 10 | 10 | ok |
| curriculum_subject | 328 | 328 | ok |
| discount | 304 | 304 | ok |
| enrollment | 1037 | 1037 | ok |
| enrollment_event | 1559 | 1559 | ok |
| invoice | 25931 | 25931 | ok |
| issued_document | 1001 | 1001 | ok |
| late_charge | 5241 | 5241 | ok |
| lms_account | 1003 | 1003 | ok |
| lms_event | 143727 | 143727 | ok |
| lms_quiz | 25104 | 25104 | ok |
| lms_quiz_attempt | 18857 | 18857 | ok |
| payment | 24380 | 24380 | ok |
| person | 1000 | 1000 | ok |
| price | 330 | 330 | ok |
| protocol | 3911 | 3911 | ok |
| protocol_event | 14201 | 14201 | ok |
| protocol_message | 8960 | 8960 | ok |
| service | 18 | 18 | ok |
| subject | 260 | 260 | ok |
| term | 12 | 12 | ok |
| term_enrollment | 4637 | 4637 | ok |
| unit | 27 | 27 | ok |

## Memória = oráculo independente

| verificação | memória/SQL | oráculo | igual |
| --- | ---: | ---: | --- |
| pessoas | 1000 | 1000 | ok |
| matrículas | 1037 | 1037 | ok |
| contratos resolvidos | 1065 | 1065 | ok |
| protocolos ligados | 3911 | 3911 | ok |
| protocolos sem vínculo | 22 | 22 | ok |
| protocolos por regra | {'ra_cpf': 3455, 'guardian_payer': 73, 'cpf_brand_unique': 337, 'guardian_payer_unique': 29, 'cpf_brand_by_date': 17} | {'ra_cpf': 3455, 'guardian_payer': 73, 'cpf_brand_unique': 337, 'guardian_payer_unique': 29, 'cpf_brand_by_date': 17} | ok |
| contas LMS ligadas | 1003 | 1003 | ok |
| PROTOCOL_REF encontrados | [229, 229] | [229, 229] | ok |
| tentativas do LMS | 18857 | 18857 | ok |
| simulados abertos sem tentativa | 1217 | 1217 | ok |
| simulados abertos com tentativa restante | 1615 | 1615 | ok |
| provas com nota encerradas sem tentativa | 0 | 0 | ok |

## Protocolos por regra de ligação

| regra | protocolos |
| --- | ---: |
| cpf_brand_by_date | 17 |
| cpf_brand_unique | 337 |
| guardian_payer | 73 |
| guardian_payer_unique | 29 |
| ra_cpf | 3455 |
| sem vínculo | 22 |

## Números do case

- `PROTOCOL_REF` que existem em protocolo: 229/229
- Snapshot financeiro (oráculo): 273/273
- Provas que valem nota encerradas sem tentativa: 0
- Simulados abertos sem tentativa: 1217
- Simulados abertos com tentativa restante: 1615
- Logins de analista ausentes de toda tabela carregada: 29 (presentes: 0)

## Resolução de identidade

| etapa | sem resolução | duplicados (fica o primeiro) |
| --- | ---: | ---: |
| pessoas | 0 | 0 |
| matrículas | 0 | 0 |
| contratos | 0 | 0 |

## Invariantes

- toda linha com person_key aponta para uma pessoa: ok
- toda linha com enrollment_key aponta para uma matrícula: ok
- enrollment_key único: ok
- nenhuma tabela canônica vazia além das esperadas: ok

## Cobertura temporal (início de cada fonte)

| fonte | domínio | desde |
| --- | --- | --- |
| sag | matricula_eventos | 2020-12-12 |
| sag | notas | 2021-02-01 |
| sag | rematricula | 2021-02-01 |
| secretary | secretaria | 2022-03-01 |
| lms | lms | 2023-01-15 |
| finance | financeiro | 2021-01-01 |
