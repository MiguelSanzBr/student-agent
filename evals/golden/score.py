"""Pontuação determinística de um item: fatos exigidos (do oráculo), proibidos, ferramentas,
rascunho e recusa. Multi-turno é 1 item que passa só se todos os turnos passam."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from evals.golden.constants import REFUSAL_PREFIX

REFERENCE_YEAR = 2026
_MONTHS = ("janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro "
           "dezembro").split()  # fmt: skip
_LONG_DATE = re.compile(rf"\b(\d{{1,2}}) de ({'|'.join(_MONTHS)})(?: de (\d{{4}}))?\b")


@dataclass(frozen=True)
class TurnRecord:
    """O que o cliente observou num turno (SSE `answer`/`draft`/`debug`/`done`)."""

    answer: str
    tools_called: tuple[str, ...] = ()
    draft: bool = False
    status: str = "ok"
    trace_id: str = ""
    pre_guard_answer: str | None = None
    cost_degraded: bool = False


@dataclass(frozen=True)
class Check:
    text: str
    expected_tools: tuple[str, ...] = ()
    required: tuple[str, ...] = ()  # fatos já formatados, vindos de oracle_fn
    forbidden: tuple[str, ...] = ()
    expect_draft: bool = False
    expect_refusal: bool = False
    oracle_fns: tuple[str, ...] = ()
    args: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DatasetItem:
    id: str
    template_id: str
    category: str
    split: str
    steps: tuple[Check, ...]  # 1 passo = turno único


@dataclass(frozen=True)
class ScoreResult:
    passed: bool
    reasons: tuple[str, ...] = ()


def is_refusal(answer: str) -> bool:
    return answer.lstrip().startswith(REFUSAL_PREFIX)


def normalize(text: str) -> str:
    """Minúsculas e datas por extenso -> dd/mm/aaaa (ano de referência se omitido)."""

    def date(m: re.Match[str]) -> str:
        year = int(m.group(3) or REFERENCE_YEAR)
        return f"{int(m.group(1)):02d}/{_MONTHS.index(m.group(2)) + 1:02d}/{year}"

    return _LONG_DATE.sub(date, text.lower())


def _count(fact: str, text: str) -> int:
    # fronteiras que impedem "ativa" em "inativa", "2" em "2028/2" ou em "R$ 1.234,56"
    rx = rf"(?<![\w.,/$]){re.escape(fact.lower())}(?![\w/]|[.,]\d)"
    return len(re.findall(rx, text))


def _score_turn(c: Check, t: TurnRecord) -> list[str]:
    out: list[str] = []
    text = normalize(t.answer)
    refusal = is_refusal(t.answer)
    if c.expect_refusal and not refusal:
        out.append("recusa esperada ausente")
    if refusal and not c.expect_refusal:
        out.append("recusa indevida")
    for tool in c.expected_tools:
        if tool not in t.tools_called:
            out.append(f"ferramenta esperada não chamada: {tool}")
    if c.expect_draft != t.draft:
        out.append("rascunho esperado ausente" if c.expect_draft else "rascunho indevido")
    need: dict[str, int] = {}
    for f in c.required:
        need[f] = need.get(f, 0) + 1
    for f, n in need.items():
        if _count(f, text) < n:
            out.append(f"fato ausente: {f}")
    out += [f"proibido presente: {f}" for f in c.forbidden if f.lower() in text]
    if not t.answer.strip():
        out.append("resposta vazia")
    return out


def score_item(item: DatasetItem, turns: Sequence[TurnRecord]) -> ScoreResult:
    if len(turns) != len(item.steps):
        return ScoreResult(False, (f"esperados {len(item.steps)} turnos, vieram {len(turns)}",))
    reasons: list[str] = []
    for i, (c, t) in enumerate(zip(item.steps, turns, strict=True), 1):
        reasons += [f"t{i}: {r}" for r in _score_turn(c, t)]
    return ScoreResult(not reasons, tuple(reasons))
