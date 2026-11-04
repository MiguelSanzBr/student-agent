from app.tools.base import ENROLLMENT, Tool, ToolSpec, not_implemented, schema

TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "get_academic_profile",
            "Perfil acadêmico da matrícula: curso, situação, período e progresso.",
            schema({"enrollment": ENROLLMENT}),
        ),
        not_implemented,
        "academico",
    ),
    Tool(
        ToolSpec(
            "list_course_results",
            "Lista resultados por disciplina (nota, frequência, situação).",
            schema(
                {
                    "enrollment": ENROLLMENT,
                    "term": {"type": "string", "description": "Semestre, ex.: 2026/1"},
                    "result": {"type": "string", "enum": ["aprovado", "reprovado", "cursando"]},
                    "subject": {"type": "string", "description": "Nome da disciplina"},
                }
            ),
        ),
        not_implemented,
        "academico",
    ),
    Tool(
        ToolSpec(
            "get_curriculum_progress",
            "Progresso na matriz curricular da matrícula.",
            schema({"enrollment": ENROLLMENT}),
        ),
        not_implemented,
        "academico",
    ),
)
