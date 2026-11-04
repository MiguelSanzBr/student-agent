# Qualidade: dev rodada 1

- SHA `678270e124bbbadb42c9951f9df40a7128d90181`; modelo `claude-haiku-5-5`; texto: base
- itens 553; inválidos (fora do denominador) 58; refeitos 58; rodada abortada: não

## Primário (pass@1, turno único)
- 49.1% [36.9%; 61.2%] (n=428); meta (limite inferior >= 85%): não atingida

## Por categoria (n sempre)
- abertura_negativa: 15/17
- abertura_positiva: 13/34
- academico: 35/45
- ambigua: 12/18
- catalogo: 41/45
- consulta_pedido: 4/8
- cruzamento: 5/27
- dado_contraditorio: 9/18
- divergencia: 1/18
- financeiro: 4/45
- fora_cobertura: 9/18
- fora_escopo: 23/27
- lms: 2/36
- premissa_falsa: 7/18
- protocolos: 24/36
- sem_resposta: 6/18

## Por causa de erro
- dados: 23
- redacao: 115
- roteamento: 117
- guard_bloqueou_certa: 11

## Vistos x não vistos
- vistos(shared): 49.1% [36.9%; 61.2%] (n=428)
- nao_vistos(test_only): n/d

## Recusa e cruzamento
- recusa correta: 71.1% [35.5%; 97.8%] (n=45)
- sem recusa indevida: 100.0% [100.0%; 100.0%] (n=383)
- cruzamento: 18.5% [0.0%; 55.6%] (n=27)

## Multi-turno (secundário): 28.4% (n=67)

## Sensibilidade (scorer validado)
pendente: rótulos do autor (labels.csv)

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
