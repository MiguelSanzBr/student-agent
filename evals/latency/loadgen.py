"""Gerador de carga em laço aberto (chegadas de Poisson) e critérios da escada (a)."""

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Outcome:
    status: int  # 200 admitido; 503 rejeitado
    latency_s: float
    retry_after_s: float | None = None
    retried: bool = False  # a requisição ao LLM precisou de retry
    cut: bool = False  # turno cortado, por qualquer causa


@dataclass(frozen=True)
class StepResult:
    rate: float
    sent: int
    ok: int
    rejected: int
    rejected_no_retry_after: int
    retries: int
    cut: int
    latencies: list[float] = field(default_factory=list)  # só dos admitidos
    reasons: Mapping[str, int] = field(default_factory=dict)  # recusas por motivo (quota, slots...)

    @property
    def p95(self) -> float:
        xs = sorted(self.latencies)
        return xs[math.ceil(0.95 * len(xs)) - 1] if xs else 0.0


@dataclass(frozen=True)
class LadderResult:
    steps: list[StepResult]


Handler = Callable[[float, random.Random], Outcome]


def run_ladder(
    rates: Sequence[float], duration_s: float, *, seed: int, handler: Handler
) -> LadderResult:
    """Cada degrau: chegadas de Poisson (intervalos exponenciais) em tempo simulado; o `handler`
    recebe o instante da chegada e devolve o desfecho. Não espera a resposta (laço aberto)."""
    rng = random.Random(seed)
    steps = []
    for rate in rates:
        t, out = rng.expovariate(rate), []
        while t < duration_s:
            out.append(handler(t, rng))
            t += rng.expovariate(rate)
        ok = [o for o in out if o.status == 200]
        rej = [o for o in out if o.status == 503]
        steps.append(
            StepResult(
                rate,
                len(out),
                len(ok),
                len(rej),
                sum(o.retry_after_s is None for o in rej),
                sum(o.retried for o in out),
                sum(o.cut for o in out),
                [o.latency_s for o in ok],
            )  # fmt: skip
        )
    return LadderResult(steps)


def ladder_a_pass(
    result: LadderResult, baseline_p95: float, *, plateau: float = 0.9, p95_factor: float = 1.1,
    retry_max: float = 0.10, cut_max: float = 0.005,
) -> bool:  # fmt: skip
    """Escada (a): goodput em platô (o último degrau não cai abaixo de `plateau` do pico); toda
    rejeição é 503 com Retry-After; retries ≤ `retry_max`; cortes ≤ `cut_max`; p95 dos admitidos no
    degrau mais alto (2× a cota) ≤ `p95_factor` × baseline; nesse degrau há recusas e a maioria é
    pelo motivo `quota` (senão mediu as vagas, não o limitador). Padrões =
    `model_constants.ladder_a`
    do slo_targets.yaml (teste os amarra). Escada vazia ou sem admitidos no topo não passa."""
    s = result.steps
    if not s or s[-1].ok == 0:
        return False
    top = s[-1]
    return (
        top.ok >= plateau * max(x.ok for x in s)  # duração igual por degrau: ok compara goodput
        and all(x.rejected_no_retry_after == 0 for x in s)
        and all(x.retries <= retry_max * x.sent for x in s)
        and all(x.cut <= cut_max * x.sent for x in s)
        and top.p95 <= p95_factor * baseline_p95
        and top.rejected > 0
        and top.reasons.get("quota", 0) > 0.5 * top.rejected
    )
