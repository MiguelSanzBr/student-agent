from app.tools.base import ENROLLMENT, Tool, ToolSpec, not_implemented, schema

TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "list_protocols",
            "Lista os protocolos do aluno (sem descrições).",
            schema({"status": {"type": "string", "description": "Situação do protocolo"}}),
        ),
        not_implemented,
        "protocolos",
    ),
    Tool(
        ToolSpec(
            "get_protocol",
            "Detalha um protocolo do aluno pelo número.",
            schema(
                {"numero": {"type": "string", "description": "Número do protocolo"}}, ("numero",)
            ),
        ),
        not_implemented,
        "protocolos",
    ),
    Tool(
        ToolSpec(
            "draft_protocol",
            "Prepara o rascunho de um protocolo para confirmação do aluno no web.",
            schema(
                {
                    "service_code": {
                        "type": "string",
                        "description": "Código do serviço, ex.: DOC-001",
                    },
                    "enrollment": ENROLLMENT,
                    "subject": {
                        "type": "string",
                        "description": "Código da disciplina, quando o serviço exigir",
                    },
                    "description": {"type": "string", "description": "Descrição do pedido"},
                },
                ("service_code",),
            ),
        ),
        not_implemented,
        "protocolos",
        read_only=False,
    ),
)
