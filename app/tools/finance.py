from app.tools.base import ENROLLMENT, Tool, ToolSpec, not_implemented, schema

TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "get_financial_summary",
            "Resumo financeiro da matrícula: total para quitar, vencidos e próximo vencimento.",
            schema({"enrollment": ENROLLMENT}),
        ),
        not_implemented,
        "financeiro",
    ),
    Tool(
        ToolSpec(
            "list_invoices",
            "Lista faturas da matrícula, com vencimento, saldo e valor para quitar.",
            schema(
                {
                    "enrollment": ENROLLMENT,
                    "status": {"type": "string", "enum": ["aberta", "vencida", "paga", "parcial"]},
                    "period": {"type": "string", "description": "Competência AAAA-MM"},
                }
            ),
        ),
        not_implemented,
        "financeiro",
    ),
    Tool(
        ToolSpec(
            "list_agreements",
            "Lista acordos de renegociação da matrícula.",
            schema({"enrollment": ENROLLMENT}),
        ),
        not_implemented,
        "financeiro",
    ),
)
