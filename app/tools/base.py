from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from core.scope import StudentScope

if TYPE_CHECKING:
    from psycopg import AsyncConnection

    from app.config import AppSettings
    from app.infra.stores.drafts import DraftStore

EntityKind = Literal["matricula", "disciplina", "documento", "financeiro", "fatura", "acordo",
                     "desconto", "protocolo", "servico", "prova", "lms", "curso", "calendario",
                     "cobertura"]  # fmt: skip
Domain = Literal["academico", "financeiro", "protocolos", "lms", "catalogo"]


@dataclass(frozen=True)
class EntityRecord:
    kind: EntityKind
    id: str
    aliases: tuple[str, ...]
    fields: Mapping[str, str]

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"


@dataclass(frozen=True)
class ToolCallTrace:
    name: str
    args: Mapping[str, str]
    returned_records: int


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class DraftKey:
    service_code: str
    subject_code: str | None


@dataclass(frozen=True)
class DraftCard:
    draft_id: str
    token: str
    key: DraftKey
    service_name_pt: str
    enrollment_label: str | None
    fee: str
    due_date: str
    description: str
    warning: str

    def public(self) -> dict[str, str]:
        """Vai no SSE, nunca ao LLM: sem o token."""
        return {
            "draft_id": self.draft_id,
            "service_code": self.key.service_code,
            "subject_code": self.key.subject_code or "",
            "service_name_pt": self.service_name_pt,
            "enrollment_label": self.enrollment_label or "",
            "fee": self.fee,
            "due_date": self.due_date,
            "description": self.description,
            "warning": self.warning,
        }


@dataclass(frozen=True)
class ToolOutput:
    data: Mapping[str, Any]
    records: tuple[EntityRecord, ...] = ()
    untrusted: bool = False
    draft: DraftCard | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(r, EntityRecord) for r in self.records):
            raise TypeError("records exige EntityRecord")


@dataclass(frozen=True)
class ToolContext:
    scope: StudentScope
    settings: AppSettings
    channel: Literal["web", "a2a"]
    conversation_id: str
    tainted: bool
    round: int
    offer_other_enrollment: bool
    today: date
    db: Callable[[], AbstractAsyncContextManager[AsyncConnection[Any]]]  # conexão por chamada
    drafts: DraftStore


Handler = Callable[[ToolContext, Mapping[str, Any]], Awaitable[ToolOutput]]


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    handler: Handler
    domain: Domain
    read_only: bool = True
    allowed: Callable[[ToolContext], str | None] = lambda ctx: None  # motivo da recusa ou None


async def not_implemented(ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
    raise NotImplementedError


def schema(
    props: Mapping[str, Mapping[str, Any]], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    """JSON Schema fechado (additionalProperties: false) de uma ferramenta."""
    return {"type": "object", "properties": dict(props), "required": list(required),
            "additionalProperties": False}  # fmt: skip


ENROLLMENT = {
    "type": "string",
    "enum": ["1", "2", "3"],
    "description": "Índice da matrícula (1, 2 ou 3). Omita para a matrícula principal.",
}


def _norm(s: str) -> str:
    d = unicodedata.normalize("NFKD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold().strip()


def index_records(records: Iterable[EntityRecord]) -> dict[str, EntityRecord]:
    return {r.key: r for r in records}


def find_entity(index: Mapping[str, EntityRecord], mention: str) -> EntityRecord | None:
    """Casa por id ou alias, sem acento e sem caixa."""
    m = _norm(mention)
    for r in index.values():
        if m == _norm(r.id) or any(m == _norm(a) for a in r.aliases):
            return r
    return None
