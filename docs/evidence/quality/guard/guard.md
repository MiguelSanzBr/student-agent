# Guard: perturbação cega e guard x scorer

- semente do conjunto cego: 20261106 (medido 1x); 170 mutados, 43 controles
- recall (IC agrupado por template de mutação): 91.8% [78.8%; 100.0%]
- bloqueio indevido nos controles: 0/43 0.0% [0.0%; 8.2%]

## Recall por template (Wilson)
- absent_deadline: 10/10 100.0% [72.2%; 100.0%]
- absent_discount: 10/10 100.0% [72.2%; 100.0%]
- count_change: 9/10 90.0% [59.6%; 98.2%]
- date_from_other_student: 10/10 100.0% [72.2%; 100.0%]
- date_shift_days: 10/10 100.0% [72.2%; 100.0%]
- date_shift_month: 10/10 100.0% [72.2%; 100.0%]
- flip_negation: 7/10 70.0% [39.7%; 89.2%]
- invented_promise: 10/10 100.0% [72.2%; 100.0%]
- invented_protocol_number: 10/10 100.0% [72.2%; 100.0%]
- money_digit: 10/10 100.0% [72.2%; 100.0%]
- money_from_other_student: 10/10 100.0% [72.2%; 100.0%]
- money_off_by_amount: 10/10 100.0% [72.2%; 100.0%]
- money_swap_in_answer: 10/10 100.0% [72.2%; 100.0%]
- percent_change: 10/10 100.0% [72.2%; 100.0%]
- period_change: 10/10 100.0% [72.2%; 100.0%]
- protocol_number_digit: 10/10 100.0% [72.2%; 100.0%]
- status_word: 0/10 0.0% [0.0%; 27.8%]

## Rascunhos do dev x scorer
- bloqueio indevido em rascunho que o scorer aprova: 151/169 89.3% [83.8%; 93.2%]
- perturbados: 236 (sem cartão/oráculo único: 304)
- guard_bloqueia_scorer_reprova: 133
- guard_bloqueia_scorer_passa: 101
- guard_passa_scorer_reprova: 0
- guard_passa_scorer_passa: 2
