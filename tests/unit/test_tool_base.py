from uuid import uuid4

import pytest

from app.tools.base import (
    FERRAMENTA_INDISPONIVEL,
    TAINT_NO_DRAFT,
    EntityRecord,
    ToolError,
    ToolSpec,
    orientation,
    resolve_enrollment,
    schema,
    service_record,
    validate_args,
)
from core.scope import EnrollmentRef, StudentScope


def _ref(i: str, status: str, primary: bool) -> EnrollmentRef:
    return EnrollmentRef(i, uuid4(), f"Curso {i}", "UniSaoPaulo", "UniSaoPaulo",  # type: ignore[arg-type]
                         "presencial", status, primary)  # fmt: skip


SCOPE = StudentScope(
    uuid4(), (_ref("1", "ACTIVE", True), _ref("2", "GRADUATED", False)), ("UniSaoPaulo",)
)
ONE = StudentScope(uuid4(), (_ref("1", "ACTIVE", True),), ("UniSaoPaulo",))


def test_index_resolution_and_nonexistent() -> None:
    assert resolve_enrollment(SCOPE, "2", None).index == "2"
    assert resolve_enrollment(SCOPE, None, None).is_primary
    with pytest.raises(ToolError) as e:
        resolve_enrollment(ONE, "3", None)
    assert e.value.code == "MATRICULA_INEXISTENTE"


def test_prefer_status() -> None:
    assert resolve_enrollment(SCOPE, None, "GRADUATED").index == "2"
    assert resolve_enrollment(SCOPE, None, "SUSPENDED").index == "1"  # sem match: a principal
    assert resolve_enrollment(SCOPE, "1", "GRADUATED").index == "1"  # índice explícito vence


SPEC = ToolSpec("t", "d", schema({"enrollment": {"type": "string", "enum": ["1", "2", "3"]}}))


def test_extra_arg_rejected() -> None:
    for bad in ({"cpf": "1"}, {"enrollment": "9"}):
        with pytest.raises(ToolError) as e:
            validate_args(SPEC, bad)
        assert e.value.code == "ARGUMENTO_INVALIDO"
    assert validate_args(SPEC, {"enrollment": "2"}) == {"enrollment": "2"}


def test_fixed_messages() -> None:
    assert TAINT_NO_DRAFT == (
        "Para abrir uma solicitação agora, comece uma nova conversa (botão Nova conversa)."
    )
    assert FERRAMENTA_INDISPONIVEL


def test_orientation_and_service_record() -> None:
    o = orientation("ACA-012")
    assert o["taxa"] == "R$ 0,00" and o["prazo"] and "Portal" in o["canais"]
    assert "só orienta" in o["porque_agente_nao_executa"]
    g = orientation("GEN-090")
    assert "Falar com a secretaria" in g["orientacao"] and g["prazo"] in g["orientacao"]
    r = service_record("GEN-090")
    assert isinstance(r, EntityRecord) and r.key == "servico:GEN-090"
    assert "Falar com a secretaria" in r.aliases
    assert {"orientacao", "taxa", "prazo", "canais"} <= set(r.fields)
