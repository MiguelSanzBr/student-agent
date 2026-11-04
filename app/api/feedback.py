"""POST /feedback: 👍/👎; o 👎 guarda por 7 dias só as saídas tipadas do turno (nunca texto)."""

import contextlib
import json
import re
from datetime import timedelta
from functools import cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from psycopg import errors
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import get_person_cookie
from app.api.protocols import same_origin
from app.data.db import with_scope
from app.infra.stores.turn_outputs import TurnOutputCache
from app.obs.annotations import annotate
from app.obs.metrics import METRICS
from core.clock import wall_now

router = APIRouter()
RETENTION = timedelta(days=7)
_SERVICE = re.compile(r"[A-Z]{3}-\d{3}")
_OMITTED = "[omitido]"


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    useful: bool
    comment: str | None = Field(default=None, max_length=500)


@cache
def _props() -> dict[str, dict[str, Any]]:
    from app.tools.registry import build_tools

    return {t.spec.name: dict(t.spec.parameters["properties"]) for t in build_tools()}


def _enumerated(tool: str, key: str, value: str) -> bool:
    """Só vale o que o servidor consegue enumerar (enum/padrão do schema ou código de serviço)."""
    prop = _props().get(tool, {}).get(key, {})
    if "enum" in prop:
        return value in prop["enum"]
    if "pattern" in prop:
        return re.fullmatch(prop["pattern"], value) is not None
    return key == "service_code" and _SERVICE.fullmatch(value) is not None


def typed_only(payload: dict[str, Any]) -> dict[str, Any]:
    """Sem a resposta e sem argumento livre (que pode citar o aluno)."""
    out = {k: v for k, v in payload.items() if k not in {"answer", "pre_guard_answer"}}
    out["tool_calls"] = [
        {**c, "args": {k: v if _enumerated(c["name"], k, str(v)) else _OMITTED
                       for k, v in c.get("args", {}).items()}}
        for c in payload.get("tool_calls", [])
    ]  # fmt: skip
    return out


@router.post("/feedback", dependencies=[Depends(same_origin)])
async def feedback(
    body: FeedbackRequest, request: Request, pk: Annotated[UUID, Depends(get_person_cookie)]
) -> dict[str, str]:
    st = request.app.state
    # o cache é por aluno: o trace de outro não aparece, e o 👎 dele não copia nada
    payload = None if body.useful else await TurnOutputCache(st.redis).get(pk, body.trace_id)
    now = wall_now()
    async with with_scope(st.pool, pk, ()) as conn:
        await conn.execute(
            "INSERT INTO agent_rw.feedback (trace_id, person_key, useful, comment)"
            " VALUES (%s, %s, %s, %s)",
            (body.trace_id, pk, body.useful, body.comment),
        )
        if payload is not None:
            # agent_app não tem SELECT: ON CONFLICT não serve; o 👎 repetido vira UniqueViolation
            with contextlib.suppress(errors.UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO agent_rw.turn_output (trace_id, person_key, created_at,"
                        " expires_at, payload) VALUES (%s, %s, %s, %s, %s::jsonb)",
                        (body.trace_id, pk, now, now + RETENTION,
                         json.dumps(typed_only(dict(payload)))),
                    )  # fmt: skip
    METRICS["agent_feedback_total"].labels(useful=str(body.useful).lower()).inc()  # type: ignore[union-attr]
    annotate(body.trace_id, "feedback", "up" if body.useful else "down",
             1.0 if body.useful else 0.0, body.comment)  # fmt: skip
    return {"status": "ok"}
