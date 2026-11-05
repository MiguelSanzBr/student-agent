# Qualidade: test rodada 1

- SHA `d6e3d8c8a395a1cc8420ec4edbb5e036fcad54dd`; modelo `claude-haiku-5-5`; texto: base
- itens 3321; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não
- rodadas do teste (todas contadas, §15): r1 d6e3d8c8

## Primário (pass@1, turno único)
- 43.5% [34.0%; 53.4%] (n=3012); meta (limite inferior >= 85%): não atingida

## P(meta) registrada antes da rodada (§4)
ICC 0.479, n=40 alunos x 87 templates, 200 sim.
- pass@1 verdadeiro 0.654: P = 0.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.704: P = 0.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.754: P = 0.00 (EP Monte Carlo 0.000)

## Por categoria (n sempre)
- abertura_negativa: 63/63
- abertura_positiva: 126/126
- abertura_protocolo_negativa: 0/80
- abertura_protocolo_positiva: 0/40
- academico: 153/360
- ambigua: 80/160
- catalogo: 104/240
- consulta_pedido: 22/63
- cruzamento: 12/200
- dado_contraditorio: 65/120
- divergencia: 25/120
- financeiro: 32/320
- fora_cobertura: 70/120
- fora_escopo: 240/240
- lms: 71/240
- premissa_falsa: 21/160
- protocolos: 160/200
- sem_resposta: 67/160

## Por causa de erro
- dados: 191
- redacao: 407
- guard_bloqueou_certa: 124
- roteamento: 1180

## Vistos x não vistos
- vistos(shared): 64.3% [53.3%; 74.8%] (n=1852)
- nao_vistos(test_only): 10.3% [0.0%; 20.7%] (n=1160)

## Recusa e cruzamento
- recusa correta: 96.9% [89.4%; 100.0%] (n=320)
- sem recusa indevida: 100.0% [100.0%; 100.0%] (n=2692)
- cruzamento: 6.0% [0.5%; 13.5%] (n=200)

## Multi-turno (secundário): 35.0% (n=309)

## Sensibilidade (scorer validado)
VPP 100.0% [86.2%; 100.0%]; VPN 33.3% [9.7%; 70.0%]; pass corrigido 77.1% [57.2%; 93.2%]

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
