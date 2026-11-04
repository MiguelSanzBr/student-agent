from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class StudentCard:
    text: str
    facts: Mapping[str, str]  # chaves "<índice>.<campo>" e "geral.<campo>"
