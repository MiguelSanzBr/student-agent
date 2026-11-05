# Qualidade: dev rodada 5

- SHA `112467c6bf61f7edd1ede4dfe6f4f0254203317f`; modelo `claude-haiku-5-5`; texto: base
- itens 553; inválidos (fora do denominador) 0; refeitos 0; rodada abortada: não

## Primário (pass@1, turno único)
- 92.0% [85.9%; 96.9%] (n=476); meta (limite inferior >= 85%): atingida

## Por categoria (n sempre)
- abertura_negativa: 19/19
- abertura_positiva: 34/38
- academico: 43/50
- ambigua: 20/20
- catalogo: 45/50
- consulta_pedido: 6/9
- cruzamento: 22/30
- dado_contraditorio: 16/20
- divergencia: 18/20
- financeiro: 47/50
- fora_cobertura: 20/20
- fora_escopo: 30/30
- lms: 39/40
- premissa_falsa: 19/20
- protocolos: 40/40
- sem_resposta: 20/20

## Por causa de erro
- redacao: 36
- guard_bloqueou_certa: 7
- dados: 15

## Vistos x não vistos
- vistos(shared): 92.0% [85.9%; 96.9%] (n=476)
- nao_vistos(test_only): n/d

## Recusa e cruzamento
- recusa correta: 100.0% [100.0%; 100.0%] (n=50)
- sem recusa indevida: 100.0% [100.0%; 100.0%] (n=426)
- cruzamento: 73.3% [30.0%; 100.0%] (n=30)

## Multi-turno (secundário): 74.0% (n=77)

## Sensibilidade (scorer validado)
pendente: rótulos do autor (labels.csv)

## Juiz (só kappa)
pendente: sem chamada ao juiz

## Utilidade
pendente: utility_labels.csv
