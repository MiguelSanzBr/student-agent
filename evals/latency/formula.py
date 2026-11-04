"""Fórmula pré-registrada de latência (spec §6.5): lognormal por componente + Monte Carlo."""

import math
import random
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import SloTargets


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
    if len(samples_s) < 2 or any(x <= 0 for x in samples_s):
        raise ValueError("amostras: precisa de ≥ 2 valores, todos > 0")
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
    overhead. TTFE = 1ª chamada que emite ferramenta (1º `progress`); card = total. O TTFE das
    classes 1 e 2+ exclui o overhead anterior ao 1º progress (admissão, prompt): ele entra
    no prior de 300 ms só do total, então o TTFE previsto é otimista por esse valor."""
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
    """Meta de cada quantil = max(meta inicial, previsto). No `card` o TTFE é a latência total."""
    out: dict[str, dict[str, float]] = {}
    for cls, q in predicted.items():
        pred = {"p50": q.p50, "p90": q.p90, "p95": q.p95, "p99": q.p99, "ttfe_p95": q.ttfe_p95}
        out[cls] = {k: max(initial.get(cls, {}).get(k, 0.0), v) for k, v in pred.items()}
        if cls == "card":
            out[cls]["ttfe_p95"] = out[cls]["p95"]
    return out


def graph_budget_s(predicted: Mapping[str, ClassQuantiles]) -> float:
    return predicted["2+"].p99 * 1.2


# Metas iniciais da spec §6.5 (o piso do max(inicial, previsto)); k de ferramentas e retry do guard
# como nos priors de docs/evidence/spikes.md.
INITIAL = {
    "card": {"p50": 3.5, "p95": 6.0},
    "1": {"p95": 8.0, "ttfe_p95": 4.0},
    "2+": {"p95": 12.0, "ttfe_p95": 4.0},
}
K_TOOLS = {"card": [0], "1": [1], "2+": [2, 2, 3, 4]}
P_GUARD_RETRY = 0.05


def refit_after_window_a(
    samples: Mapping[str, Sequence[float]], *, n: int = 200_000, seed: int = 42
) -> "SloTargets":
    """Única atualização das metas (depois da janela A): reajusta os lognormais dos componentes
    com amostra e refaz targets/ttfe/graph_budget_s pela mesma fórmula. `frozen` fica como está
    (o M4 é quem congela)."""
    from app.config import Lognormal, SloTargets, load_slo_targets

    cur = load_slo_targets()
    comps = {k: LogNormal(v.mu, v.sigma) for k, v in cur.components.items()}
    comps.update({k: fit_lognormal(v) for k, v in samples.items()})
    pred = {c: simulate_class(comps, k, P_GUARD_RETRY, n=n, seed=seed) for c, k in K_TOOLS.items()}
    tg = targets(INITIAL, pred)
    r = lambda x: round(x, 2)  # noqa: E731
    data = cur.model_dump()
    data["components"] = {k: Lognormal(mu=v.mu, sigma=v.sigma) for k, v in comps.items()}
    data["targets"] = {c: {q: r(v[q]) for q in ("p50", "p90", "p95", "p99")} for c, v in tg.items()}
    data["ttfe"] = {c: {"p95": r(v["ttfe_p95"])} for c, v in tg.items()}
    data["graph_budget_s"] = r(graph_budget_s(pred))
    return SloTargets.model_validate(data)
