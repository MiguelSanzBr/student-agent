"""Fórmula pré-registrada de latência (spec §6.5): lognormal por componente + Monte Carlo."""

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LogNormal:
    mu: float
    sigma: float


@dataclass(frozen=True)
class ClassQuantiles:
    p50: float
    p90: float
    p95: float
    p99: float
    ttfe_p95: float


def fit_lognormal(samples_s: Sequence[float]) -> LogNormal:
    logs = [math.log(x) for x in samples_s]
    return LogNormal(statistics.fmean(logs), statistics.stdev(logs))


def _q(sorted_xs: Sequence[float], p: float) -> float:
    return sorted_xs[min(len(sorted_xs) - 1, math.ceil(p * len(sorted_xs)) - 1)]


def simulate_class(
    components: Mapping[str, LogNormal],
    k_tool_calls: Sequence[int],
    p_guard_retry: float,
    *,
    n: int = 200_000,
    seed: int,
) -> ClassQuantiles:
    """Soma, por sorteio: k chamadas que emitem ferramenta (k de `k_tool_calls`) + 1 que responde
    (+1 se o guard pedir retry) + overhead (SQL, guard, infra). Componentes: tool_call, answer_call,
    overhead. TTFE = 1ª chamada que emite ferramenta (1º `progress`); card (sem ferramenta) = total."""
    rng = random.Random(seed)
    draw = {k: (lambda v=v: rng.lognormvariate(v.mu, v.sigma)) for k, v in components.items()}
    totals, ttfes = [], []
    for _ in range(n):
        k = rng.choice(k_tool_calls)
        calls = [draw["tool_call"]() for _ in range(k)]
        t = sum(calls) + draw["answer_call"]() + draw["overhead"]()
        if rng.random() < p_guard_retry:
            t += draw["answer_call"]()
        totals.append(t)
        ttfes.append(calls[0] if k else t)
    totals.sort()
    ttfes.sort()
    return ClassQuantiles(
        _q(totals, 0.5), _q(totals, 0.9), _q(totals, 0.95), _q(totals, 0.99), _q(ttfes, 0.95)
    )


def targets(
    initial: Mapping[str, Mapping[str, float]], predicted: Mapping[str, ClassQuantiles]
) -> dict[str, dict[str, float]]:
    """Meta de cada quantil = max(meta inicial, previsto)."""
    out: dict[str, dict[str, float]] = {}
    for cls, q in predicted.items():
        pred = {"p50": q.p50, "p90": q.p90, "p95": q.p95, "p99": q.p99, "ttfe_p95": q.ttfe_p95}
        out[cls] = {k: max(initial.get(cls, {}).get(k, 0.0), v) for k, v in pred.items()}
    return out


def graph_budget_s(predicted: Mapping[str, ClassQuantiles]) -> float:
    return predicted["2+"].p99 * 1.2
