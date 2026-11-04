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
pass@1 corrigido pela validação do scorer (secao 12): VPP e VPN entram como Beta (Wilson como
prior de largura) e propagam-se ao bootstrap; IC por bootstrap x Beta.

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
O autor rotula às cegas 30 respostas do teste: 24 aprovadas e 6 reprovadas pelo scorer
(sorteio com semente fixa). Saem VPP e VPN com intervalo de Wilson.

## 13. Juiz
Rubrica binária com gabarito, rodada sobre o teste **só para medir a concordância (kappa) com o
scorer**. Não é métrica de qualidade e não entra em nenhuma meta.

## 14. Efeitos do 2º nível de corte
O 2º nível de corte (degradação do agente sob pressão de custo/latência) é analisado nos
roteiros-núcleo (multi-turno 1 a 4): reporta-se pass dos 4 núcleos com e sem o 2º nível, por
contagem exata (n = 4 x alunos), sem inferência além do IC de dois fatores.

## 15. Rodadas do teste
Toda rodada do teste (inclusive abortada ou repetida por vazamento) é contada e declarada em
`quality.md`; só a 1ª no SHA congelado é o primário.

## 16. Dataset e holdout
- Templates: >= 50, dos quais >= 15 `test_only`, escritos por redator cego (sem prompt,
  ferramentas ou guard) e cifrados com `age` para a chave pública do autor
  (`evals/holdout/author.pub`). Só o `.age` e o `manifest.json` ficam no git.
- Fatos exigidos (`required`) vêm só do oráculo (`oracle_fn`); nenhum template os escreve.
- Os 40 alunos de teste (semente 20261103) são disjuntos dos 10 golden.
- 20 itens sorteados do dataset são conferidos à mão pelo autor (`oracle_check.csv`).
