# Plano de análise pré-registrado (qualidade)

Este arquivo é pré-registrado: o hash dele, do dataset `shared`, do `score.py` e do arquivo
cifrado do holdout (`evals/golden/dataset.sha256`) é carimbado por OpenTimestamps na quinta.
Depois do carimbo, qualquer mudança vira adendo datado no fim, nunca edição.

## 1. Estimando primário
pass@1 do `score_item`, turno único, no conjunto de teste (40 alunos x todos os templates
aplicáveis), **1ª execução no SHA congelado**. Multi-turno (8 roteiros, 1 item por roteiro,
passa só se todos os turnos passam) é secundário.

## 2. Intervalo de confiança
Bootstrap de dois fatores (aluno x template), reamostrando os dois eixos, estratificado pelas
cotas (modalidade x {ativo, inativo} e os traços raros), 10.000 réplicas, IC de 95% (percentis).
A amostra é declaradamente enriquecida nos traços raros; o resultado não estima a população.

## 3. Meta
Limite inferior do IC >= 85% **no primário**. Não há meta em subgrupos nem no multi-turno.

## 4. Poder e P(meta)
O ICC (aluno e template) é estimado no dev (10 golden x templates `shared`). Antes da rodada de
teste, simula-se a P(limite inferior >= 85%) sob o pass@1 verdadeiro estimado no dev e sob
+/- 5 p.p.; os números são gravados em `quality.md` antes da execução. A rodada não é ajustada
a esse resultado.

## 5. Turno inválido e rodada abortada
- 429/5xx do provedor e turno sob degradação de custo (`debug.cost_degraded`): refeitos 1x; o
  2º insucesso é registrado como inválido e fica fora do denominador, com a contagem declarada.
- Rejeição de admissão antes do stream (429/503 com `Retry-After`) não é tentativa: o runner
  espera e reenvia.
- Teto duro de custo aborta a rodada; ela é registrada e contada como rodada do teste.
- Estouro do orçamento do grafo é falha (conta no denominador).

## 6. Gabarito
O resultado primário vale como rodou. Correção de gabarito (erro do oráculo achado depois) só
entra como **análise de sensibilidade**, com o item listado num anexo.

## 7. Recusa e cruzamento
Reportados com IC pelo mesmo bootstrap de dois fatores: recusa correta (itens
`expect_refusal`), recusa indevida (recusa em item que não a espera; detectada pelo prefixo
fixo `REFUSAL_PREFIX`) e itens de cruzamento.

## 8. Sensibilidade
pass@1 corrigido pela validação do scorer (seção 12). A validação rende contagens
TP, FP (aprovadas pelo scorer que o autor rotulou erradas/corretas) e TN, FN (idem nas
reprovadas). Priors: VPP ~ Beta(1 + TP, 1 + FP) e VPN ~ Beta(1 + TN, 1 + FN), com prior
uniforme Beta(1, 1). Em cada réplica b do bootstrap de dois fatores (seção 2), com pass@1
observado p_b, sorteia-se (vpp_b, vpn_b) das duas Betas e calcula-se
`p_corr_b = vpp_b * p_b + (1 - vpn_b) * (1 - p_b)`. O IC de 95% é o percentil 2,5 e 97,5 das
10.000 réplicas de `p_corr_b`. Como VPP e VPN são condicionais ao rótulo do scorer, a
amostra 24/6 (não proporcional) não os enviesa. O intervalo de Wilson (95%) só é reportado
para o próprio VPP e VPN.

## 9. Escolha do modelo
Na 1ª rodada do dev, com margem pareada >= 3 p.p. em pass@1 (mesmos itens), vence o modelo mais
forte; abaixo da margem, o mais barato. Ajuste de prompt/regra: no máximo 2 iterações, dentro
do teto de correções do sábado.

## 10. Desdobramentos
Vistos x não vistos (`shared` x `test_only`); dev x teste; por categoria, sempre com n; por
causa de erro (secao 11). Desdobramentos são descritivos, sem meta.

## 11. Causas de erro
Cada reprovação é atribuída automaticamente pelo trace a um estágio: roteamento (ferramenta
esperada não chamada), dados (saída da ferramenta diferente do oráculo), redação (dados certos,
resposta errada), guard bloqueou resposta certa (rascunho pré-guard passa no scorer), recusa
indevida, ou gabarito (marcado à mão). O estágio vira o `label` da anotação no Phoenix.

## 12. Validação do scorer
Do teste (1ª execução no SHA congelado), ordenam-se os itens por `item_id` e estratificam-se
por categoria. Sorteiam-se 24 itens aprovados e 6 reprovados pelo scorer com
`random.Random(20261104)`; a alocação por categoria é proporcional ao número de itens da
categoria em cada grupo (aprovados/reprovados), arredondada pelo método do maior resto. Se
houver menos de 6 reprovações, entram todas e os aprovados completam 30. O autor rotula às
cegas (vê pergunta e resposta, não o veredito); saem TP/FP/TN/FN, VPP e VPN com Wilson.

## 13. Juiz
Rubrica binária com gabarito, rodada sobre o teste **só para medir a concordância (kappa) com o
scorer**. Não é métrica de qualidade e não entra em nenhuma meta.

## 14. Efeitos do 2º nível de corte
O 2º nível de corte (degradação do agente sob pressão de custo/latência) é analisado nos
roteiros-núcleo (multi-turno mt-01 a mt-04). Unidade: o item (roteiro x aluno). O n é a soma
dos itens desses 4 roteiros nos alunos aos quais se aplicam (mt-02 só vale para aluno ativo;
os demais para todos os 40), no máximo 160. Compara-se o pass dos itens com e sem o 2º nível,
pareado por item, com teste exato de McNemar nos pares discordantes e a diferença pareada
com IC por bootstrap só no eixo aluno (cluster). Sem meta; descritivo.

## 15. Rodadas do teste
Toda rodada do teste (inclusive abortada ou repetida por vazamento) é contada e declarada em
`quality.md`; só a 1ª no SHA congelado é o primário.

## 16. Dataset e holdout
- Templates: >= 50, dos quais >= 15 `test_only`, escritos por redator cego (sem prompt,
  ferramentas ou guard) e cifrados com `age` para a chave pública do autor
  (`evals/holdout/author.pub`). Só o `.age` e o `manifest.json` ficam no git.
- Fatos exigidos (`required`) vêm só do oráculo (`oracle_fn`); nenhum template os escreve.
- Os 40 alunos de teste (`select_test_students`, semente 20261103) excluem os person_keys do
  `golden_login.json`; estratos modalidade x {ativo, inativo}; cotas >= 3 nos oito traços.
- 20 itens sorteados do dataset são conferidos à mão pelo autor (`oracle_check.csv`).
- Texto por item: o **texto base** (`text`) de cada template, em toda rodada primária. As
  paráfrases entram só numa rodada secundária de robustez (não é o primário): cada item usa a
  paráfrase de índice `int(sha256(item_id)[:8], 16) % len(paraphrases)`; templates sem
  paráfrase mantêm o texto base.
- Matrícula por aluno: o oráculo avalia uma matrícula por item. Para aluno com 2+ matrículas, a
  escolhida é a de menor (marca, RA) entre as ATIVAS se o aluno é ativo, senão a de menor
  (marca, RA) entre todas. O texto do template não nomeia a matrícula; um agente que pergunta
  "qual matrícula?" em vez de responder reprova o item (contado, não corrigido).
- Regra `student` (`_applies` em `build.py`): template ou roteiro com `student: ativo` só
  gera item para aluno cuja matrícula escolhida tem `ENROLLMENT_STATUS == ACTIVE`; com
  `student: inativo`, só para os demais. Sem o campo, vale para todos. Logo o denominador por
  template varia e é declarado.

## 17. Escopo e limites do scorer
O scorer é determinístico: fatos exigidos (do oráculo) com fronteiras de palavra e
multiplicidade, datas por extenso normalizadas, **fato negado na mesma oração não conta**
("não está ativa" não satisfaz "ativa"), padrões `forbidden` (regex) e ferramentas, rascunho e
recusa (prefixo fixo; recusa com valor em R$ ou data inventados reprova). O `forbidden`
automático do `build.py` inclui: status trocado (alunos com 1 matrícula), valores exclusivos da
outra matrícula da pessoa e valores monetários/datas onde o gabarito é "nada".
Leniência residual declarada: (a) negação fora da oração do fato ou parafraseada
("deixou de estar ativa") pode passar; (b) fatos soltos corretos ao lado de afirmações extras
erradas não detectadas pelos padrões passam; (c) o `forbidden` é estrito demais quando o
agente cita legitimamente a outra matrícula (reprova). A validação 24/6 (seção 12) mede (a) e
(b) como VPP/VPN; a seção 8 propaga essa incerteza.

## 18. Nota datada sobre o carimbo
2026-11-04: o carimbo OpenTimestamps (`dataset.sha256.ots`) está **pendente por decisão do
autor** (publica o hash em servidores externos). Até o carimbo existir, o commit git que
contém este arquivo e o `dataset.sha256` é a única prova temporal do pré-registro.
