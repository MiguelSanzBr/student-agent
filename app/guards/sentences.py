"""Divisão em frases, compartilhada pelo guard e pela degradação (mesmos índices)."""

import re

_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ý\"(])|\n+")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text) if s and s.strip()]
