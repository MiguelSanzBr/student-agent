# Notas das rodadas reais (2026-11-04) — fatos sem JSON próprio

Cada item diz de onde veio. As transcrições completas ficam só no volume `eval-out` (sensíveis).

- **Dev 1, 58 inválidos:** 56 são do caso 10 e 2 do caso 9 (contagem por aluno nas transcrições da
  rodada). A mesma pergunta do caso 10, refeita depois, respondeu normalmente; o PC do autor reiniciou
  logo depois da rodada, e a causa provável é a API ter caído. A dev 2 e a dev 3 tiveram 0 inválidos.
- **Dev 3, cortes pelo prazo:** 0 respostas com o texto de prazo estourado ("Estou demorando...") e 0
  com LLM indisponível nas 553 transcrições.
- **Tokens por turno:** sondas pela API com `claude-haiku-5-5` mostraram ~13,5 mil tokens de entrada por
  turno (~11,6 mil em cache) e ~0,8–1 mil de saída e raciocínio (evento `debug.usage`). Com 200 mil
  tokens/aluno/dia contando o cache, isso dá ~13 turnos por dia; a cota passou a contar só tokens fora
  do cache (~70 turnos por dia). Essas medidas viraram `est_tokens` e `turn_usage` em `slo_targets.yaml`.
- **Cache:** `cache_ratio` (tokens em cache sobre tokens de entrada) é 48% na janela B e ~86% nas sondas.
  A diferença é a carga: a janela usa 10 alunos e conversas novas (o prefixo comum fica em cache, o
  cartão e o histórico não); as sondas eram um aluno repetindo perguntas.
- **Perfil eval:** `limits.eval.tokens_per_day` subiu de 200 mil para 5 milhões (`slo_targets.yaml`)
  porque a rodada dev faria ~16 turnos por aluno sintético; o teto de gasto (US$ 40/dia) ficou igual.
- **Oráculo vazio:** contagens em `analysis-dev-r1.md` e `analysis-dev-r2.md` (análise das
  transcrições contra `templates.yaml`, `oracle.py` e `score.py`).
- **Ataques:** os templates que falharam e os motivos estão em `../security.json` (`attacks[*].reasons`).
- **Teste r2, sensibilidade:** o `quality.md`/`quality.json` da r2 saíram com a linha de sensibilidade
  calculada com `evals/golden/labels.csv`, os rótulos da r1. Os rótulos são da resposta da r1, não da
  r2, então essa linha não vale. A amostra da r2 (mesmo sorteio §12 sobre os resultados dela) foi
  rotulada às cegas por um agente de IA (`evals/golden/labels-r2.csv`) e a rodada foi re-sumarizada
  (`test-r2/quality.resummarized.*`): VPP 95,8%, VPN 50,0%. Os arquivos originais ficam como saíram.
- **Teste r2, protocolos (leitura do autor, sem rótulo cego):** cada template `test_only` vale 40
  itens (um por aluno), então um critério do redator cego move 40 itens de uma vez.
  `abertura_protocolo_positiva` (1/40): 22 respostas prepararam o rascunho e reprovaram só porque o
  gabarito exige a ferramenta `catalogo` (o `draft_protocol` já confere o serviço no catálogo); as 17
  sem rascunho são de alunos sem matrícula ativa (formado, trancado, evadido, cancelado, transferido),
  para quem a declaração não é emitida, e a resposta diz isso. `abertura_protocolo_negativa` (33/80):
  40 reprovam só por não chamar `academico`; 6 rascunhos indevidos e 1 resposta vazia são falhas reais.
  `consulta_pedido` (`test_only`, 40 itens): o pedido corresponde ao serviço "Segunda via de boleto"
  do catálogo, mas o agente só informa o saldo e não prepara o rascunho. Falha real do agente nos 40
  (12 desses alunos têm fatura vencida).
