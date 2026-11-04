from app.tools.base import ENROLLMENT, Tool, ToolSpec, not_implemented, schema

TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "get_lms_activity",
            "Atividade no AVA: acessos, simulados, próxima avaliação e pendências.",
            schema(
                {
                    "enrollment": ENROLLMENT,
                    "period": {"type": "string", "description": "Período, ex.: 2026/2"},
                }
            ),
        ),
        not_implemented,
        "lms",
    ),
)
