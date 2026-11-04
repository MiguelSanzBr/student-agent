from app.tools.base import Tool, ToolSpec, not_implemented, schema

TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "get_catalog",
            "Consulta o catálogo da marca: cursos, disciplinas, preços, calendário e serviços.",
            schema(
                {
                    "kind": {
                        "type": "string",
                        "enum": ["curso", "disciplina", "preco", "calendario", "servico"],
                    },
                    "query": {"type": "string", "description": "Termo de busca"},
                    "modality": {"type": "string", "enum": ["presencial", "ead"]},
                },
                ("kind",),
            ),
        ),
        not_implemented,
        "catalogo",
    ),
)
