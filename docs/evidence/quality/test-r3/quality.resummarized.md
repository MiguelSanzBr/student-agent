# Qualidade: test rodada 3

- SHA `17f8704167a0a600d16c08a2a76f060ec6a76cad`; modelo `claude-haiku-5-5`; texto: base
- itens 3321; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não
- rodadas do teste (todas contadas, §15): r1 d6e3d8c8; r2 dad845ec; r3 17f87041

## Primário (pass@1, turno único)
- 86.2% [79.7%; 92.0%] (n=3012); meta (limite inferior >= 85%): não atingida

## P(meta) registrada antes da rodada (§4)
ICC 0.216, n=40 alunos x 87 templates, 200 sim.
- pass@1 verdadeiro 0.870: P = 0.24 (EP Monte Carlo 0.030)
- pass@1 verdadeiro 0.920: P = 1.00 (EP Monte Carlo 0.000)
- pass@1 verdadeiro 0.970: P = 1.00 (EP Monte Carlo 0.000)

## Por categoria (n sempre)
- abertura_negativa: 62/63
- abertura_positiva: 126/126
- abertura_protocolo_negativa: 12/80
- abertura_protocolo_positiva: 0/40
- academico: 329/360
- ambigua: 160/160
- catalogo: 145/240
- consulta_pedido: 31/63
- cruzamento: 161/200
- dado_contraditorio: 103/120
- divergencia: 111/120
- financeiro: 303/320
- fora_cobertura: 113/120
- fora_escopo: 238/240
- lms: 229/240
- premissa_falsa: 154/160
- protocolos: 200/200
- sem_resposta: 120/160

## Por causa de erro
- redacao: 109
- dados: 181
- guard_bloqueou_certa: 56
- recusa_indevida: 6
- roteamento: 159

## Vistos x não vistos
- vistos(shared): 87.8% [80.8%; 93.8%] (n=1852)
- nao_vistos(test_only): 83.7% [70.4%; 95.3%] (n=1160)

## Recusa e cruzamento
- recusa correta: 98.8% [96.2%; 100.0%] (n=320)
- sem recusa indevida: 99.8% [99.2%; 100.0%] (n=2692)
- cruzamento: 80.5% [49.5%; 98.5%] (n=200)

## Multi-turno (secundário): 68.9% (n=309)

## Sensibilidade (scorer validado)
VPP 95.8% [79.8%; 99.3%]; VPN 33.3% [9.7%; 70.0%]; pass corrigido 88.2% [76.4%; 95.9%]

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
