"""Normalização e parsing de identificadores e valores vindos dos sistemas de origem."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

SP = ZoneInfo("America/Sao_Paulo")

_RA_RE = re.compile(r"([0-9]{5})-?([0-9])")
_REF_RE = re.compile(r"([0-9]{1,5})(?:-?([0-9]))?")
_LMS_RE = re.compile(r"(PRES|EAD)_[0-9]{5}")


def ra_check_digit(base5: str) -> str:
    """DV do RA: módulo 11 com pesos 6..2; resto 0 ou 1 vira 0."""
    total = sum(int(d) * w for d, w in zip(base5, (6, 5, 4, 3, 2), strict=True))
    dv = 11 - total % 11
    return "0" if dv >= 10 else str(dv)


def normalize_ra(raw: str) -> str | None:
    """RA canônico de 6 dígitos (base + DV), aceitando hífen; None se o DV não confere."""
    m = _RA_RE.fullmatch(raw.strip())
    if m is None or ra_check_digit(m[1]) != m[2]:
        return None
    return m[1] + m[2]


def normalize_academic_ref(raw: str) -> str | None:
    """Referência com ou sem DV (sem DV, calcula-o). Base curta é completada à esquerda."""
    m = _REF_RE.fullmatch(raw.strip())
    if m is None:
        return None
    base = m[1].zfill(5)
    dv = ra_check_digit(base)
    return base + dv if m[2] in (None, dv) else None


def normalize_cpf(raw: str) -> str | None:
    """CPF só com dígitos, com zeros à esquerda restaurados. Não valida o DV."""
    d = digits(raw)
    return d.zfill(11) if 0 < len(d) <= 11 else None


def lms_login(raw: str) -> str | None:
    s = raw.strip().upper()
    return s if _LMS_RE.fullmatch(s) else None


def digits(v: str) -> str:
    return "".join(c for c in v if c in "0123456789")


def parse_epoch(v: str) -> datetime | None:
    n = int(v)
    return None if n == 0 else datetime.fromtimestamp(n, UTC)


def parse_iso(v: str) -> datetime:
    return datetime.fromisoformat(v)


def parse_naive_local(v: str) -> datetime:
    """Horário de parede de Brasília (sem offset na origem) convertido para datetime com fuso."""
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is not None:
        raise ValueError(f"esperado horário sem fuso: {v!r}")
    return dt.replace(tzinfo=SP)


def parse_date(v: str, fmt: str) -> date:
    return datetime.strptime(v, fmt).date()


def parse_brl(v: str) -> int:
    """Valor em reais no formato brasileiro ('R$ 1.234,56') para centavos."""
    s = v.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    cents = Decimal(s) * 100
    if cents % 1:
        raise ValueError(f"valor com fração de centavo: {v!r}")
    return int(cents)


def parse_bool(v: str) -> bool:
    s = v.strip().upper()
    if s in {"Y", "TRUE", "1"}:
        return True
    if s in {"N", "FALSE", "0"}:
        return False
    raise ValueError(f"booleano inesperado: {v!r}")
