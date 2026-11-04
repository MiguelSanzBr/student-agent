from app.tools.base import EntityRecord

SAMPLE_RECORDS = (
    EntityRecord(
        "disciplina",
        "CALC1",
        ("Cálculo I", "calculo i"),
        {"resultado": "reprovado", "media_final": "4,2", "frequencia": "80%"},
    ),
    EntityRecord(
        "disciplina", "ALGO", ("Algoritmos",), {"resultado": "aprovado", "media_final": "8,5"}
    ),
    EntityRecord(
        "financeiro",
        "1",
        ("matrícula 1",),
        {
            "total_para_quitar": "R$ 49.508,43",
            "principal_vencido": "R$ 42.636,26",
            "faturas_vencidas": "32",
        },
    ),
    EntityRecord(
        "fatura",
        "7067951",
        ("7067951",),
        {
            "status": "parcial",
            "saldo": "R$ 91,33",
            "para_quitar": "R$ 99,12",
            "vencimento": "05/11/2026",
        },
    ),
    EntityRecord(
        "protocolo",
        "20260022332",
        ("20260022332",),
        {"servico": "Revisão de nota", "aberto_em": "08/10/2026", "status": "em análise"},
    ),
    EntityRecord(
        "desconto", "D1", ("bolsa",), {"tipo": "bolsa", "percentual": "20%", "vigente": "sim"}
    ),
    EntityRecord(
        "acordo", "AG2024000128", ("AG2024000128",), {"status": "rompido", "parcelas": "10"}
    ),
    EntityRecord("cobertura", "lms", ("AVA", "LMS"), {"desde": "15/01/2023"}),
)
ACTIVE_DEAL = EntityRecord(
    "acordo", "AG2026000340", ("AG2026000340",), {"status": "ativo", "parcelas": "10"}
)
LMS_2026_2 = EntityRecord("lms", "2026/2", ("2026/2",), {"acessos": "3"})
FIN031_ORIENTATION = EntityRecord(
    "servico",
    "FIN-031",
    ("Renegociação de dívida",),
    {"orientacao": "parcelamento em até 12x, conforme análise", "taxa": "R$ 0,00"},
)
SAMPLE_FACTS = {
    "1.total_para_quitar": "R$ 49.508,43",
    "2.total_para_quitar": "R$ 0,00",
    "1.previsao_conclusao": "2028/1",
    "1.proxima_avaliacao": "05/12/2026",
}
