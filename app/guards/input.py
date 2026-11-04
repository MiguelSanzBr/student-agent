"""Guard de entrada: normalização e teto de tamanho (spec §6)."""

import unicodedata

from app.config import load_slo_targets

_KEEP = {"\n", "\t"}


class InputTooLong(ValueError):
    pass


def normalize_input(text: str) -> str:
    """NFKC, sem caracteres invisíveis; acima do teto levanta `InputTooLong`."""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(c for c in t if c in _KEEP or unicodedata.category(c) not in ("Cf", "Cc", "Cs"))
    if len(t) > load_slo_targets().limits["default"].input_chars:
        raise InputTooLong(len(t))
    return t
