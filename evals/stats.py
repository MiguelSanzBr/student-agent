"""Estatística pré-registrada (analysis_plan.md §2, §8, §12): bootstrap de dois fatores,
Wilson, VPP/VPN com priors Beta, pass corrigido, kappa e P(meta). Só stdlib, determinística."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import NormalDist

Z95 = 1.959963984540054


@dataclass(frozen=True)
class CI:
    low: float
    high: float
    point: float


@dataclass(frozen=True)
class ItemResult:
    item_id: str
    student: str
    template: str
    passed: bool
    category: str = ""


@dataclass(frozen=True)
class Label:
    """Rótulo do autor (validação do scorer, §12): o scorer aprovou? o autor achou correta?"""

    scorer_passed: bool
    author_correct: bool


@dataclass(frozen=True)
class TurnTrace:
    """O que o runner observou no turno, para atribuir causa de erro (§11)."""

    answer: str
    tools_called: tuple[str, ...] = ()
    tool_outputs: Mapping[str, object] = field(default_factory=dict)
    oracle_outputs: Mapping[str, object] = field(default_factory=dict)
    pre_guard_answer: str | None = None
    gabarito_marked: bool = False
    step: int = 0  # passo reprovado (multi-turno)
    tool_values: Mapping[str, Sequence[str]] | None = None  # debug.tool_values; None = não visto


def wilson(k: int, n: int) -> CI:
    if n == 0:
        return CI(0.0, 1.0, 0.0)
    p, z2 = k / n, Z95**2
    d = 1 + z2 / n
    c = (p + z2 / (2 * n)) / d
    h = Z95 * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / d
    return CI(max(0.0, c - h), min(1.0, c + h), p)


def _counts(labels: Sequence[Label]) -> tuple[int, int, int, int]:
    tp = sum(x.scorer_passed and x.author_correct for x in labels)
    fp = sum(x.scorer_passed and not x.author_correct for x in labels)
    tn = sum(not x.scorer_passed and not x.author_correct for x in labels)
    fn = sum(not x.scorer_passed and x.author_correct for x in labels)
    return tp, fp, tn, fn


def ppv_npv(labels: Sequence[Label]) -> tuple[CI, CI]:
    tp, fp, tn, fn = _counts(labels)
    return wilson(tp, tp + fp), wilson(tn, tn + fn)


def corrected_pass(p: float, ppv: float, npv: float) -> float:
    return ppv * p + (1 - npv) * (1 - p)


def _percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    i = q * (len(xs) - 1)
    lo = int(i)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (i - lo)


def _boot_p(
    results: Sequence[ItemResult], strata: Mapping[str, str], n_boot: int, rng: random.Random
) -> Iterator[float]:
    """pass@1 de cada réplica: alunos reamostrados dentro do estrato, templates globalmente."""
    students_by_stratum: dict[str, list[str]] = defaultdict(list)
    for s in sorted({r.student for r in results}):
        students_by_stratum[strata[s]].append(s)
    templates = sorted({r.template for r in results})
    cells = [(r.student, r.template, r.passed) for r in results]
    for _ in range(n_boot):
        ws: dict[str, int] = defaultdict(int)
        for group in students_by_stratum.values():
            for s in rng.choices(group, k=len(group)):
                ws[s] += 1
        wt: dict[str, int] = defaultdict(int)
        for t in rng.choices(templates, k=len(templates)):
            wt[t] += 1
        num = den = 0
        for s, t, ok in cells:
            w = ws.get(s, 0) * wt.get(t, 0)
            den += w
            num += w * ok
        if den:
            yield num / den


def two_way_bootstrap_ci(
    results: Sequence[ItemResult],
    *,
    strata: Mapping[str, str],
    n_boot: int = 10_000,
    seed: int,
) -> CI:
    point = sum(r.passed for r in results) / len(results)
    ps = list(_boot_p(results, strata, n_boot, random.Random(seed)))
    return CI(_percentile(ps, 0.025), _percentile(ps, 0.975), point)


def corrected_pass_ci(
    results: Sequence[ItemResult],
    labels: Sequence[Label],
    *,
    strata: Mapping[str, str],
    n_boot: int = 10_000,
    seed: int,
) -> CI:
    """§8: por réplica, p_b do bootstrap e (vpp_b, vpn_b) das Betas com prior uniforme."""
    tp, fp, tn, fn = _counts(labels)
    rng = random.Random(seed)
    corr = [
        corrected_pass(p, rng.betavariate(1 + tp, 1 + fp), rng.betavariate(1 + tn, 1 + fn))
        for p in _boot_p(results, strata, n_boot, rng)
    ]
    point = corrected_pass(
        sum(r.passed for r in results) / len(results), (1 + tp) / (2 + tp + fp),
        (1 + tn) / (2 + tn + fn),
    )  # fmt: skip
    return CI(_percentile(corr, 0.025), _percentile(corr, 0.975), point)


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def p_target(
    icc: float, n_students: int, n_templates: int, p0: float, lb: float = 0.85,
    *, n_sim: int, seed: int,
) -> float:  # fmt: skip
    """P(limite inferior do IC >= lb) simulando grade aluno x template com ICC (probit)."""
    rng = random.Random(seed)
    thr = NormalDist().inv_cdf(p0)
    w, e = math.sqrt(icc / 2), math.sqrt(1 - icc)
    strata = {f"s{s}": "x" for s in range(n_students)}
    hits = 0
    for i in range(n_sim):
        a = [rng.gauss(0, 1) for _ in range(n_students)]
        b = [rng.gauss(0, 1) for _ in range(n_templates)]
        res = [
            ItemResult(f"{s}-{t}", f"s{s}", f"t{t}",
                       w * a[s] + w * b[t] + e * rng.gauss(0, 1) < thr)
            for s in range(n_students) for t in range(n_templates)
        ]  # fmt: skip
        hits += two_way_bootstrap_ci(res, strata=strata, n_boot=200, seed=seed + i).low >= lb
    return hits / n_sim
