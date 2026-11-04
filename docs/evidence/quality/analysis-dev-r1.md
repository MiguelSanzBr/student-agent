# Análise das falhas — dev rodada 1 (claude-haiku-5-5; pass@1 49,1%, n=428; 266 fail)

Analista: subagente sonnet (só leitura). Gravado pelo controlador (o subagente não pôde escrever).

## Maiores fontes
1. R$ proibido com oráculo vazio — 109 itens. 7 dos 9 alunos não têm fatura vencida;
   `oracle.ideal_answer` dá 0 fatos e `build._EMPTY_FORBIDS` proíbe qualquer `r\$\s*\d`, inclusive
   "R$ 0,00" e o próximo vencimento (verdadeiros). Classe (c): rigidez do oráculo pré-registrado.
2. Respondeu do cartão sem chamar a ferramenta — 52 (lms 26, protocolos 12, financeiro 8,
   previsão 6), mais 48 dentro do item 1. Classe (a): prompt.
3. Fato exigido que a pergunta não pediu — 54 (`oracle_lms` exige 3 fatos; `oracle_courses`
   situação + percentual; protocolos a contagem em dígito). (c), contornável por prompt.
4. R$ voluntário em pedido vago/sem resposta — 27. (a).
5. Rascunho: draft_protocol não chamado (22) ou serviço errado (2) — 24. (a).
Depois: guarda degradou resposta correta 11 (b); não recusou 9 + recusa com dado do aluno 4 (a);
regex proibida 3 (c). Texto além do limite: 0. Recusa indevida: 0.

## (c) Rigidez do scorer/oráculo — só documentar (pré-registrado e carimbado)
- Oráculo financeiro vazio proíbe qualquer R$ (109); idem catálogo/protocolos vazios.
- `oracle_lms` exige 3 fatos por qualquer pergunta de AVA (~60); `oracle_courses` situação e
  percentual (~32); `oracle_catalog` preço e ano (mt-05 t1, 7); `oracle_protocols` contagem em dígito.
- `NEG_WINDOW` = 3 palavras (pf-02); `escopo: uma_matricula` proíbe citar a outra matrícula.
- Mitigação prevista no plano: validação do scorer por 30 rótulos às cegas do autor (VPP/VPN) e
  pass@1 corrigido como sensibilidade.

## (b) Bugs
1. Guarda não aceita a data de referência do cartão (`app/data/card.py` não põe `today` em
   `facts`); 28 transcrições perdem "31/10/2026"; 3 viram guard_bloqueou_certa.
2. Falso positivo de compromisso ("parcelas por semestre" do catálogo; "use o botão...") —
   `app/rules/commitments.py` / `_PROMISE` da guarda: guarda congelada, não mexer.
3. status/polaridade removem frases certas de AVA ("situação realizada", "0 de 3 usadas",
   contagem derivada) — guarda congelada; possível mitigação do lado da ferramenta (evidência).
4. `get_catalog` não aceita marca (aluno de uma marca pergunta de outra) — decisão de produto.
5. Cartão sem previsão de conclusão — mitigado pela regra "consulte a ferramenta".
6. `draft_protocol` devolve rascunho de outro serviço quando o código está errado, sem sinalizar.
7. Retry da guarda gera "Peço desculpas pela resposta anterior" — cosmético.

## Proposta de prompt (a) — 2 iterações; nenhuma regra de segurança afrouxada
Iteração 1 (teto 87; realista ~45–55): L1 ferramenta primeiro; L3 rascunho na hora; L4 recusa
sem dado do aluno e assuntos sem fonte; L5 pedido vago / dado inexistente sem listar valores;
L6 (não citar data de referência) substituída pela correção do bug 1.
Iteração 2 (teto +81; realista +35–50): L2 completude (AVA 3 dados; matrícula situação +
percentual; protocolos contagem e números).
L7 (não recomendada, não aplicada): "sem vencidas, não cite R$" — ajuste ao gabarito, esconde do
aluno o próximo vencimento verdadeiro.

## Inválidos
58 (56 do caso10), já investigados: provável queda da API perto do reinício do PC.
