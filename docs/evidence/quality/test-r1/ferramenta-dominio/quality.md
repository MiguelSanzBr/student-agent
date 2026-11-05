# Qualidade: test rodada 1

- SHA `d6e3d8c8a395a1cc8420ec4edbb5e036fcad54dd`; modelo `claude-haiku-5-5`; texto: base
- itens 3321; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não
- rodadas do teste (todas contadas, §15): r1 d6e3d8c8

## Primário (pass@1, turno único)
- 75.8% [67.5%; 83.5%] (n=3012); meta (limite inferior >= 85%): não atingida

## P(meta) registrada antes da rodada (§4)
ICC 0.479, n=40 alunos x 87 templates, 200 sim.
- pass@1 verdadeiro 0.654: P = 0.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.704: P = 0.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.754: P = 0.00 (EP Monte Carlo 0.000)

## Por categoria (n sempre)
- abertura_negativa: 63/63
- abertura_positiva: 126/126
- abertura_protocolo_negativa: 71/80
- abertura_protocolo_positiva: 23/40
- academico: 313/360
- ambigua: 160/160
- catalogo: 144/240
- consulta_pedido: 22/63
- cruzamento: 92/200
- dado_contraditorio: 105/120
- divergencia: 65/120
- financeiro: 152/320
- fora_cobertura: 110/120
- fora_escopo: 240/240
- lms: 150/240
- premissa_falsa: 101/160
- protocolos: 200/200
- sem_resposta: 147/160

## Por causa de erro
- dados: 191
- redacao: 407
- guard_bloqueou_certa: 124
- roteamento: 207

## Vistos x não vistos
- vistos(shared): 64.3% [53.3%; 74.8%] (n=1852)
- nao_vistos(test_only): 94.2% [86.0%; 99.9%] (n=1160)

## Recusa e cruzamento
- recusa correta: 96.9% [89.4%; 100.0%] (n=320)
- sem recusa indevida: 100.0% [100.0%; 100.0%] (n=2692)
- cruzamento: 46.0% [8.5%; 83.0%] (n=200)

## Multi-turno (secundário): 35.0% (n=309)

## Sensibilidade (scorer validado)
pendente: rótulos do autor (labels.csv)

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
