# Análise das falhas — dev rodada 2 (claude-haiku-5-5, iteração 1; pass@1 62,4%, n=476)
Analista: subagente sonnet (só leitura); gravado pelo controlador.
- 553 itens = 476 turno único (297 pass) + 77 multiturno (35 pass).
- Restantes: R$ proibido com oráculo vazio 84 (c); fato exigido ausente ~110 com sobreposição (a)+(c);
  ferramenta não chamada 21 1T + 25 MT (a); rascunho ausente 5 (a); recusa indevida 3 — PIOROU, conflito
  entre a regra nova de recusa e a de "dado inexistente" (a); guarda degradou certa 10 (b, congelada);
  escopo uma_matricula 5 (c); regex "termina este ano" 4 (c); catálogo sem a marca pedida ~9 (b).
- Fracos: divergência 4/20 (R$ vazio + não dá os dois contadores); premissa falsa 5/20 (não chama a ferramenta
  em pergunta com premissa); dado contraditório 9/20 (falta percentual); cruzamento 5/30; AVA 5/40 (responde
  em palavras, some com os números exigidos); financeiro 7/50 (35 por R$ vazio); acadêmico 39/50 (ac-03 exige
  situação); catálogo 42/50 (get_catalog só usa a marca da matrícula).
- Multiturno: na 2ª pergunta o modelo reaproveita o número anterior e não rechama a ferramenta.
Iteração 2 (última): R-A ferramenta a cada pergunta, inclusive após resposta anterior e com premissa; R-B
completude em algarismos; R-C recusa só fora do atendimento, dado inexistente = "não consta" sem R$; R-D
rascunho mesmo com protocolo aberto do mesmo serviço. Bug (b): get_catalog aceita a marca pedida.
Estimativa realista 1T ~69% (67–72%); teto ~85% sem mexer no oráculo. L7 continua vetada.
Sensibilidade a documentar: 46 itens 1T falham SÓ por R$ em oráculo vazio (~+9,7 p.p.).
