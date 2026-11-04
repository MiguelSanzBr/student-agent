# Qualidade: dev rodada 3

- SHA `9ffa74fddc6b3e86e5c4665fe6e79d009181f4c2`; modelo `claude-haiku-5-5`; texto: base
- itens 553; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não

## Primário (pass@1, turno único)
- 70.4% [59.0%; 80.6%] (n=476); meta (limite inferior >= 85%): não atingida

## Por categoria (n sempre)
- abertura_negativa: 19/19
- abertura_positiva: 36/38
- academico: 38/50
- ambigua: 20/20
- catalogo: 45/50
- consulta_pedido: 8/9
- cruzamento: 2/30
- dado_contraditorio: 17/20
- divergencia: 8/20
- financeiro: 11/50
- fora_cobertura: 18/20
- fora_escopo: 30/30
- lms: 20/40
- premissa_falsa: 4/20
- protocolos: 40/40
- sem_resposta: 19/20

## Por causa de erro
- dados: 20
- redacao: 105
- guard_bloqueou_certa: 30
- roteamento: 29

## Vistos x não vistos
- vistos(shared): 70.4% [59.0%; 80.6%] (n=476)
- nao_vistos(test_only): n/d

## Recusa e cruzamento
- recusa correta: 96.0% [84.0%; 100.0%] (n=50)
- sem recusa indevida: 100.0% [100.0%; 100.0%] (n=426)
- cruzamento: 6.7% [0.0%; 26.7%] (n=30)

## Multi-turno (secundário): 44.2% (n=77)

## Sensibilidade (scorer validado)
pendente: rótulos do autor (labels.csv)

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
