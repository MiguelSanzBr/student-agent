# Qualidade: test rodada 2

- SHA `dad845eca7972012dcf5e3a8aa0833ee24c0f86a`; modelo `claude-haiku-5-5`; texto: base
- itens 3321; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não
- rodadas do teste (todas contadas, §15): r1 d6e3d8c8; r2 dad845ec

## Primário (pass@1, turno único)
- 73.5% [65.1%; 81.5%] (n=3012); meta (limite inferior >= 85%): não atingida

## P(meta) registrada antes da rodada (§4)
ICC 0.479, n=40 alunos x 87 templates, 200 sim.
- pass@1 verdadeiro 0.654: P = 0.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.704: P = 0.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.754: P = 0.00 (EP Monte Carlo 0.000)

## Por categoria (n sempre)
- abertura_negativa: 63/63
- abertura_positiva: 126/126
- abertura_protocolo_negativa: 33/80
- abertura_protocolo_positiva: 1/40
- academico: 311/360
- ambigua: 160/160
- catalogo: 144/240
- consulta_pedido: 21/63
- cruzamento: 92/200
- dado_contraditorio: 107/120
- divergencia: 68/120
- financeiro: 152/320
- fora_cobertura: 108/120
- fora_escopo: 238/240
- lms: 155/240
- premissa_falsa: 96/160
- protocolos: 200/200
- sem_resposta: 140/160

## Por causa de erro
- dados: 194
- redacao: 423
- guard_bloqueou_certa: 146
- roteamento: 235

## Vistos x não vistos
- vistos(shared): 63.8% [52.9%; 74.2%] (n=1852)
- nao_vistos(test_only): 89.1% [77.4%; 99.1%] (n=1160)

## Recusa e cruzamento
- recusa correta: 95.6% [87.2%; 100.0%] (n=320)
- sem recusa indevida: 100.0% [100.0%; 100.0%] (n=2692)
- cruzamento: 46.0% [8.5%; 83.0%] (n=200)

## Multi-turno (secundário): 35.0% (n=309)

## Sensibilidade (scorer validado)
VPP 95.8% [79.8%; 99.3%]; VPN 50.0% [18.8%; 81.2%]; pass corrigido 81.1% [68.1%; 91.9%]

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
