from __future__ import annotations

import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from jsonschema import Draft202012Validator

from core.format import fmt_brl
from core.ontology import Ontology
from core.scope import EnrollmentRef, StudentScope

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
    conversation_id: str  # só para o confirm; nunca sai no public()
    enrollment_index: str  # "1".."3"; idem

    def public(self) -> dict[str, str]:
        """Vai no SSE, nunca ao LLM: sem o token, conversation_id nem enrollment_index."""
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


def norm(s: str) -> str:
    d = unicodedata.normalize("NFKD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold().strip()


def index_records(records: Iterable[EntityRecord]) -> dict[str, EntityRecord]:
    return {r.key: r for r in records}


def find_entity(index: Mapping[str, EntityRecord], mention: str) -> EntityRecord | None:
    """Casa por id ou alias, sem acento e sem caixa."""
    m = norm(mention)
    for r in index.values():
        if m == norm(r.id) or any(m == norm(a) for a in r.aliases):
            return r
    return None


# Mensagens fixas do servidor (spec §6.1). T18 devolve em `allowed()`, T22 só reexporta.
TAINT_NO_DRAFT = "Para abrir uma solicitação agora, comece uma nova conversa (botão Nova conversa)."
FERRAMENTA_INDISPONIVEL = "Esta ferramenta não está disponível agora."


class ToolError(Exception):
    """Erro de ferramenta com código estável (o texto vai ao LLM, nunca o stack)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_enrollment(
    scope: StudentScope, index: str | None, prefer_status: str | None
) -> EnrollmentRef:
    """Índice explícito > matrícula com `prefer_status` > principal."""
    if index is not None:
        for e in scope.enrollments:
            if e.index == index:
                return e
        raise ToolError("MATRICULA_INEXISTENTE")
    if prefer_status is not None:
        for e in scope.enrollments:  # já em ordem: a mais relevante/recente primeiro
            if e.status == prefer_status:
                return e
    for e in scope.enrollments:
        if e.is_primary:
            return e
    raise ToolError("MATRICULA_INEXISTENTE")  # escopo sem matrículas


def validate_args(spec: ToolSpec, args: Mapping[str, Any]) -> dict[str, Any]:
    if next(Draft202012Validator(dict(spec.parameters)).iter_errors(dict(args)), None):
        raise ToolError("ARGUMENTO_INVALIDO")
    return dict(args)


@lru_cache(maxsize=1)
def default_ontology() -> Ontology:
    return Ontology.load(Path(__file__).resolve().parents[2] / "ontology")


def orientation(service: str, onto: Ontology | None = None) -> dict[str, str]:
    """Prazo, taxa, canais e por que o agente não executa."""
    s = (onto or default_ontology()).service(service)
    h = s.sla_hours
    prazo = f"{h // 24} dia{'s' if h > 24 else ''}" if h % 24 == 0 else f"{h} horas"
    taxa = fmt_brl(s.fee_cents)
    canais = ", ".join(s.channels)
    if service == "GEN-090":
        texto = f"{s.ui_label}: prazo de {prazo}."
    else:
        texto = f"{s.name_pt}: prazo de {prazo}, taxa {taxa}. Canais: {canais}."
    out = {"orientacao": texto, "prazo": prazo, "taxa": taxa, "canais": canais}
    if s.why_not_agent:
        out["porque_agente_nao_executa"] = s.why_not_agent
    return out


def service_record(service: str, onto: Ontology | None = None) -> EntityRecord:
    s = (onto or default_ontology()).service(service)
    return EntityRecord(
        "servico", service, tuple(dict.fromkeys((s.name_pt, s.ui_label))),
        orientation(service, onto),
    )  # fmt: skip
