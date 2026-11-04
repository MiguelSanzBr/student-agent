"""Fontes de promessa: condições presentes em orientação de serviço ou acordo ativo (spec §5.5)."""

import re
from collections.abc import Iterable

from app.tools.base import EntityRecord

Condition = str

_LEXICON = (
    (re.compile(r"parcel|dividi", re.I), "parcelamento"),
    (re.compile(r"desconto", re.I), "desconto"),
    (re.compile(r"isen[cçt]", re.I), "isenção"),
    (re.compile(r"perd[aãoõ]", re.I), "perdão"),
    (re.compile(r"prorrog|(?:estend|ampli|dilat)\w*\s+(?:o\s+)?prazo", re.I), "prorrogação"),
    (re.compile(r"sem juros", re.I), "sem juros"),
)
_NX = re.compile(r"\b(\d{1,2})\s*(?:x\b|parcelas|vezes)", re.I)


_TIMES = re.compile(r"\b(?:em|ate|até)\s+(?:ate\s+|até\s+)?(\d{1,2})\s+vezes", re.I)


def conditions_in(text: str) -> set[Condition]:
    found = {label for rx, label in _LEXICON if rx.search(text)}
    times = _TIMES.findall(text)  # "em 6 vezes" é parcelamento mesmo sem a palavra
    if times:
        found |= {"parcelamento", *(f"parcelamento {n}x" for n in times)}
    if "parcelamento" in found:
        found |= {f"parcelamento {n}x" for n in _NX.findall(text)}
    return found


def commitment_sources(records: Iterable[EntityRecord]) -> frozenset[Condition]:
    out: set[Condition] = set()
    for r in records:
        if r.kind == "servico":
            out |= conditions_in(r.fields.get("orientacao", ""))
        elif r.kind == "acordo" and r.fields.get("status") == "ativo":
            out |= {"parcelamento"}
            if n := r.fields.get("parcelas"):
                out.add(f"parcelamento {n}x")
    return frozenset(out)
