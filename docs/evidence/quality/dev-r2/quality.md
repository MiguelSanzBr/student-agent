# Qualidade: dev rodada 2

- SHA `340b5283a310d7a9099dcb90cf1eaeab184028ba`; modelo `claude-haiku-5-5`; texto: base
- itens 553; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não

## Primário (pass@1, turno único)
- 62.4% [49.6%; 74.5%] (n=476); meta (limite inferior >= 85%): não atingida

## Por categoria (n sempre)
- abertura_negativa: 19/19
- abertura_positiva: 36/38
- academico: 39/50
- ambigua: 20/20
- catalogo: 42/50
- consulta_pedido: 6/9
- cruzamento: 5/30
- dado_contraditorio: 9/20
- divergencia: 4/20
- financeiro: 7/50
- fora_cobertura: 20/20
- fora_escopo: 29/30
- lms: 5/40
- premissa_falsa: 5/20
- protocolos: 38/40
- sem_resposta: 13/20

## Por causa de erro
- dados: 29
- redacao: 154
- guard_bloqueou_certa: 10
- roteamento: 25
- recusa_indevida: 3

## Vistos x não vistos
- vistos(shared): 62.4% [49.6%; 74.5%] (n=476)
- nao_vistos(test_only): n/d

## Recusa e cruzamento
- recusa correta: 98.0% [88.0%; 100.0%] (n=50)
- sem recusa indevida: 99.3% [97.2%; 100.0%] (n=426)
- cruzamento: 16.7% [0.0%; 53.3%] (n=30)

## Multi-turno (secundário): 45.5% (n=77)

## Sensibilidade (scorer validado)
pendente: rótulos do autor (labels.csv)

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
