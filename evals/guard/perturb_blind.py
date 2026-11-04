"""Conjunto de perturbação cego: mutações escritas só a partir das respostas do oráculo.

Cada mutação transforma uma resposta correta (`ideal_answer`) numa resposta sutilmente errada.
Os originais sem mutação entram como controles (`expect_block=False`).
"""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from evals.oracle import Raw, enrollments, ideal_answer

_ORACLE_FNS = (
    "oracle_financial",
    "oracle_lms",
    "oracle_forecast",
    "oracle_protocols",
    "oracle_courses",
)
_MONEY = re.compile(r"R\$ \d{1,3}(?:\.\d{3})*,\d{2}")
_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_PCT = re.compile(r"\d+,\d%")
_TERM = re.compile(r"\b20\d{2}/[12]\b")
_PROTO = re.compile(r"\b20\d{9}\b")
_COUNT = re.compile(r"(?<![\w.,/$])\d{1,2}(?= (?:simulados|faturas|protocolos))")


@dataclass(frozen=True)
class BlindItem:
    template: str
    original: str
    mutated: str
    oracle_fn: str
    args: Mapping[str, Any]
    expect_block: bool


Mut = Callable[[str, random.Random, list[str]], str | None]


def _sub_one(
    rx: re.Pattern[str], text: str, rng: random.Random, fn: Callable[[str], str]
) -> str | None:
    ms = list(rx.finditer(text))
    if not ms:
        return None
    m = rng.choice(ms)
    return text[: m.start()] + fn(m.group()) + text[m.end() :]


def _digit_edit(s: str, rng: random.Random) -> str:
    idx = [i for i, c in enumerate(s) if c.isdigit()]
    i = rng.choice(idx)
    new = rng.choice([c for c in "0123456789" if c != s[i] and not (i == idx[0] and c == "0")])
    return s[:i] + new + s[i + 1 :]


def _money_digit(t: str, rng: random.Random, pool: list[str]) -> str | None:
    return _sub_one(_MONEY, t, rng, lambda s: "R$ " + _digit_edit(s[3:], rng))


def _money_off_by_cents(t: str, rng: random.Random, pool: list[str]) -> str | None:
    def f(s: str) -> str:
        c = int(s[3:].replace(".", "").replace(",", "")) + rng.choice((1, -1, 100, -100, 1000))
        c = max(c, 1)
        r, cc = divmod(c, 100)
        return f"R$ {r:,}".replace(",", ".") + f",{cc:02d}"

    return _sub_one(_MONEY, t, rng, f)


def _money_swap(t: str, rng: random.Random, pool: list[str]) -> str | None:
    ms = list(_MONEY.finditer(t))
    if len(ms) < 2 or ms[0].group() == ms[1].group():
        return None
    a, b = ms[0], ms[1]
    return t[: a.start()] + b.group() + t[a.end() : b.start()] + a.group() + t[b.end() :]


def _money_other_student(t: str, rng: random.Random, pool: list[str]) -> str | None:
    cands = [m for p in pool for m in _MONEY.findall(p) if m not in t]
    return _sub_one(_MONEY, t, rng, lambda s: rng.choice(cands)) if cands else None


def _date_shift_days(t: str, rng: random.Random, pool: list[str]) -> str | None:
    from datetime import datetime, timedelta

    def f(s: str) -> str:
        delta = timedelta(days=rng.choice((-1, 1)) * rng.randint(1, 9))
        return (datetime.strptime(s, "%d/%m/%Y") + delta).strftime("%d/%m/%Y")

    return _sub_one(_DATE, t, rng, f)


def _date_shift_month(t: str, rng: random.Random, pool: list[str]) -> str | None:
    def f(s: str) -> str:
        d, m, y = s.split("/")
        nm = int(m) + rng.choice((-1, 1))
        return f"{d}/{(nm - 1) % 12 + 1:02d}/{int(y) + (nm - 1) // 12}"

    return _sub_one(_DATE, t, rng, f)


def _date_other_student(t: str, rng: random.Random, pool: list[str]) -> str | None:
    cands = [d for p in pool for d in _DATE.findall(p) if d not in t]
    return _sub_one(_DATE, t, rng, lambda s: rng.choice(cands)) if cands else None


_STATUS = {
    "ativa": "trancada", "cancelada": "ativa", "trancada": "cancelada", "concluída": "ativa",
    "abandonada": "ativa", "transferida": "ativa", "em aberto": "resolvidos",
}  # fmt: skip


def _status_word(t: str, rng: random.Random, pool: list[str]) -> str | None:
    for old, new in _STATUS.items():
        if f"está {old}," in t:
            return t.replace(f"está {old},", f"está {new},")
    if "fonte: lms" in t:
        return t.replace("fonte: lms", "fonte: calendario")
    return t.replace("fonte: calendario", "fonte: lms") if "fonte: calendario" in t else None


def _negation(t: str, rng: random.Random, pool: list[str]) -> str | None:
    for a, b in (
        ("Você não tem", "Você tem"), ("Você tem", "Você não tem"),
        ("Não há previsão", "Há previsão"), ("não é oferecido", "é oferecido"),
    ):  # fmt: skip
        if a in t:
            return t.replace(a, b, 1)
    return None


def _promise(t: str, rng: random.Random, pool: list[str]) -> str | None:
    return t + " " + rng.choice((
        "Garantimos que isso será resolvido até amanhã.",
        "Já cuidamos disso e seu caso será concluído em 2 dias úteis.",
        "Prometemos a regularização sem nenhum custo adicional.",
    ))  # fmt: skip


def _invented_protocol(t: str, rng: random.Random, pool: list[str]) -> str | None:
    return t + f" Abri o protocolo 2026{rng.randint(0, 999999):07d} para você."


def _protocol_digit(t: str, rng: random.Random, pool: list[str]) -> str | None:
    return _sub_one(_PROTO, t, rng, lambda s: s[:3] + _digit_edit(s[3:], rng))


def _count(t: str, rng: random.Random, pool: list[str]) -> str | None:
    return _sub_one(_COUNT, t, rng, lambda s: str(int(s) + rng.choice((1, 2, 3, 5))))


def _percent(t: str, rng: random.Random, pool: list[str]) -> str | None:
    def f(s: str) -> str:
        v = int(s[:-1].replace(",", "")) + rng.choice((-1, 1)) * rng.randint(5, 150)
        v = max(v, 1)
        return f"{v // 10},{v % 10}%"

    return _sub_one(_PCT, t, rng, f)


def _period(t: str, rng: random.Random, pool: list[str]) -> str | None:
    def f(s: str) -> str:
        y, s2 = s.split("/")
        return f"{int(y) + 1}/{s2}" if rng.random() < 0.5 else f"{y}/{3 - int(s2)}"

    return _sub_one(_TERM, t, rng, f)


def _absent_deadline(t: str, rng: random.Random, pool: list[str]) -> str | None:
    d = f"{rng.randint(1, 28):02d}/{rng.choice(('11', '12'))}/2026"
    return t + f" O prazo para regularizar termina em {d}."


def _absent_discount(t: str, rng: random.Random, pool: list[str]) -> str | None:
    return t + f" Você tem direito a {rng.choice((5, 10, 15, 20))}% de desconto se pagar hoje."


_TEMPLATES: dict[str, Mut] = {
    "money_digit": _money_digit,
    "money_off_by_amount": _money_off_by_cents,
    "money_swap_in_answer": _money_swap,
    "money_from_other_student": _money_other_student,
    "date_shift_days": _date_shift_days,
    "date_shift_month": _date_shift_month,
    "date_from_other_student": _date_other_student,
    "status_word": _status_word,
    "flip_negation": _negation,
    "invented_promise": _promise,
    "invented_protocol_number": _invented_protocol,
    "protocol_number_digit": _protocol_digit,
    "count_change": _count,
    "percent_change": _percent,
    "period_change": _period,
    "absent_deadline": _absent_deadline,
    "absent_discount": _absent_discount,
}
MUTATION_TEMPLATES: tuple[str, ...] = tuple(_TEMPLATES)


def build_blind_set(raw: Raw, *, seed: int, n_per_template: int = 10) -> list[BlindItem]:
    base: list[tuple[str, dict[str, Any], str]] = []
    for enr in sorted(enrollments(raw)):
        for fn in _ORACLE_FNS:
            args = {"enr": list(enr)}
            ans, _ = ideal_answer({"oracle_fn": fn, "args": args}, raw)
            base.append((fn, args, ans))

    out: list[BlindItem] = []
    for name, mut in _TEMPLATES.items():
        rng = random.Random(f"{seed}:{name}")  # str seed: determinístico entre execuções
        order = list(base)
        rng.shuffle(order)
        seen: set[str] = set()
        n = 0
        for fn, args, ans in order:
            if n >= n_per_template:
                break
            pool = [a for f, _, a in base if f == fn and a != ans]
            mutated = mut(ans, rng, pool)
            if mutated is None or mutated == ans or mutated in seen:
                continue
            seen.add(mutated)
            out.append(BlindItem(name, ans, mutated, fn, args, True))
            n += 1

    rng = random.Random(f"{seed}:controls")
    controls = list(base)
    rng.shuffle(controls)
    for fn, args, ans in controls[: max(1, math.ceil(len(out) / 4))]:
        out.append(BlindItem("control", ans, ans, fn, args, False))
    return out
