"""Única fábrica de chaves Redis: o mypy rejeita `str` onde se espera `RKey`."""

from typing import Literal, NewType
from uuid import UUID

RKey = NewType("RKey", str)
RKind = Literal["conv", "draft", "ticket", "labels", "a2a_task", "rate", "budget",
                "spend", "bucket", "slots", "turn_out", "inflight"]  # fmt: skip
GLOBAL_KINDS: frozenset[RKind] = frozenset({"ticket", "labels", "spend", "bucket", "slots"})


def rkey(
    kind: RKind, scope: UUID | Literal["global"], channel: Literal["web", "a2a", "-"], id: str
) -> RKey:
    if (scope == "global") != (kind in GLOBAL_KINDS):
        raise ValueError(f"escopo {scope!r} não combina com o tipo de chave {kind!r}")
    # o id vem por último: ":" dentro dele nunca desloca kind, escopo ou canal
    return RKey(f"sa:{kind}:{scope}:{channel}:{id}")
