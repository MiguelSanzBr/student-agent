"""Extração de fatos de um texto (dinheiro, datas, períodos, ids...), igual p/ frase e evidência."""

import re
from dataclasses import dataclass
from decimal import Decimal

MONTHS = ("janeiro fevereiro março abril maio junho julho agosto setembro outubro novembro "
          "dezembro").split()  # fmt: skip


@dataclass(frozen=True)
class Tok:
    kind: str  # ClaimKind, "nx" (parcelas) ou "enr" (índice de matrícula)
    norm: str
    raw: str
    pos: int


def _dec(s: str) -> str:
    return format(Decimal(s.replace(",", ".")).normalize(), "f")


def _cents(s: str) -> str:
    whole, _, frac = s.replace("R$", "").strip().rstrip(".").replace(".", "").partition(",")
    return str(int(whole or "0") * 100 + int((frac + "0")[:2] or "0"))


def _date(d: str, m: str, y: str | None) -> str:
    return f"{int(d):02d}/{int(m):02d}" + (f"/{y}" if y else "")


_MONTH_RX = "|".join(MONTHS)
# (kind, regex, normalizador | None = só mascara). A ordem importa: o que casa sai do texto.
_RULES: tuple[tuple[str, re.Pattern[str], object], ...] = tuple(
    (k, re.compile(rx, fl), fn)
    for k, rx, fl, fn in (
        ("url", r"(?:https?://|www\.)\S+", re.I, lambda m: m[0].lower()),
        ("email", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", 0, lambda m: m[0].lower()),
        ("opaque", r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b", re.I, lambda m: m[0]),
        (
            "opaque",
            r"\b(?=[A-Za-z0-9+/]*\d)(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])"
            r"[A-Za-z0-9+/]{20,}={0,2}",
            0,
            lambda m: m[0],
        ),
        ("opaque", r"\b[0-9a-f]{24,}\b", re.I, lambda m: m[0]),
        ("cnpj", r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b", 0, lambda m: re.sub(r"\D", "", m[0])),
        ("cpf", r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", 0, lambda m: re.sub(r"\D", "", m[0])),
        (
            "opaque",
            r"\b(?:[a-z0-9-]+\.)+(?:com|net|org|gov|edu|io|app|dev|test|info|me|co|xyz|"
            r"site|online|br)\b(?:/\S*)?",
            re.I,
            lambda m: m[0].lower(),
        ),
        ("id", r"\b[A-Z]{1,5}-?\d{3,}(?:-\d+)*\b", 0, lambda m: m[0].replace("-", "")),
        (
            "phone",
            r"(?<!\d)(?:\(?\d{2}\)?\s?)?9?\d{4}-\d{4}(?!\d)",
            0,
            lambda m: re.sub(r"\D", "", m[0]),
        ),
        ("money", r"R\$\s?\d[\d.]*(?:,\d{1,2})?", 0, lambda m: _cents(m[0])),
        (
            "date",
            r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])",
            0,
            lambda m: _date(m[1], m[2], m[3]),
        ),
        (
            "date",
            rf"\b(\d{{1,2}})º?\s+de\s+({_MONTH_RX})(?:\s+de\s+(\d{{4}}))?\b",
            re.I,
            lambda m: _date(m[1], str(MONTHS.index(m[2].lower()) + 1), m[3]),
        ),
        ("period", r"(?<![\d/])(\d{4})/([12])(?![\d/])", 0, lambda m: f"{m[1]}/{m[2]}"),
        ("date", r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])", 0, lambda m: _date(m[1], m[2], None)),
        ("pct", r"\d+(?:[.,]\d+)?\s?%", 0, lambda m: _dec(re.sub(r"[\s%]", "", m[0]))),
        ("nx", r"\b(\d{1,2})\s?x\b", re.I, lambda m: m[1]),
        ("ord", r"\b\d{1,3}\s?[ªº°]", 0, None),
        ("enr", r"\bmatr[ií]culas?\s+(\d+)\b", re.I, lambda m: m[1]),
        ("decimal", r"(?<![\d.,])\d+,\d+(?![\d,])", 0, lambda m: _dec(m[0])),
        ("id", r"\b\d{1,3}(?:\.\d{3})+\b", 0, lambda m: m[0].replace(".", "")),
        ("num", r"\b\d+\b", 0, lambda m: m[0]),
    )
)


def tokenize(text: str) -> list[Tok]:
    out: list[Tok] = []
    w = text
    for kind, rx, fn in _RULES:
        for m in rx.finditer(w):
            w = w[: m.start()] + " " * (m.end() - m.start()) + w[m.end() :]
            if fn is None:
                continue
            raw, norm = text[m.start() : m.end()], fn(m)  # type: ignore[operator]
            if kind == "num":
                n = int(norm)
                if len(norm) >= 4:
                    kind_ = "period" if 1900 <= n <= 2100 else "id"
                else:
                    kind_ = "int" if n >= 13 else "small_int"
                norm = norm if kind_ in ("id",) else str(n)
            else:
                kind_ = kind
            out.append(Tok(kind_, norm, raw, m.start()))
    return sorted(out, key=lambda t: t.pos)
