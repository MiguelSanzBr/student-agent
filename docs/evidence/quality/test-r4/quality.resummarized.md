# Qualidade: test rodada 4

- SHA `4c00de488b4d981025f1176e84ac88e5af20465b`; modelo `claude-haiku-5-5`; texto: base
- itens 3321; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não
- rodadas do teste (todas contadas, §15): r1 d6e3d8c8; r2 dad845ec; r3 17f87041; r4 4c00de48

## Primário (pass@1, turno único)
- 88.0% [82.8%; 92.5%] (n=3052); meta (limite inferior >= 85%): não atingida

## P(meta) registrada antes da rodada (§4)
ICC 0.216, n=40 alunos x 87 templates, 200 sim.
- pass@1 verdadeiro 0.870: P = 0.24 (EP Monte Carlo 0.030)
- pass@1 verdadeiro 0.920: P = 1.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.970: P = 1.00 (EP Monte Carlo 0.000)

## Por categoria (n sempre)
- abertura_negativa: 63/63
- abertura_positiva: 126/126
- abertura_protocolo_negativa: 40/40
- abertura_protocolo_positiva: 79/80
- academico: 263/320
- ambigua: 120/120
- catalogo: 146/240
- consulta_pedido: 21/23
- cruzamento: 151/240
- dado_contraditorio: 61/80
- divergencia: 72/80
- financeiro: 334/360
- fora_cobertura: 118/120
- fora_escopo: 235/240
- lms: 257/280
- premissa_falsa: 142/160
- previsao: 79/80
- protocolos: 238/240
- sem_resposta: 140/160

## Por causa de erro
- dados: 179
- redacao: 133
- guard_bloqueou_certa: 97
- recusa_indevida: 21
- roteamento: 10

## Vistos x não vistos
- vistos(shared): 86.9% [79.7%; 93.2%] (n=1852)
- nao_vistos(test_only): 89.6% [82.8%; 95.1%] (n=1200)

## Recusa e cruzamento
- recusa correta: 98.1% [95.0%; 100.0%] (n=360)
- sem recusa indevida: 99.2% [97.4%; 100.0%] (n=2692)
- cruzamento: 62.9% [35.4%; 87.5%] (n=240)

## Multi-turno (secundário): 72.9% (n=269)

## Sensibilidade (scorer validado)
VPP 87.5% [69.0%; 95.7%]; VPN 50.0% [18.8%; 81.2%]; pass corrigido 80.5% [66.0%; 91.4%]

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
