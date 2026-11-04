from dataclasses import dataclass
from typing import Literal

from app.tools.base import Domain

ClaimKind = Literal["money", "date", "period", "pct", "decimal", "int", "small_int", "id",
                    "cpf", "cnpj", "email", "phone", "url", "opaque", "status", "polarity",
                    "action", "commitment"]  # fmt: skip
FailureKind = ClaimKind | Literal["entity_without_record", "length", "canary", "prompt_ngram"]


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    sentence: int
    token: str
    entity: str | None


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    failures: tuple[Failure, ...]
    domain: Domain | None

    def kinds(self) -> frozenset[FailureKind]:
        return frozenset(f.kind for f in self.failures)
