"""Bancada de latência (spec §6.5): previsto × medido em todos os quantis, por classe.

    python -m evals.latency.bench --per-class 100 --window B --model gemini-3.8-flash
    python -m evals.latency.bench --runner sim --per-class 20 --window A   # ensaio em processo

O `--runner sim` roda o `LoadTestLLM` (sem gastar, sem rede); o runner contra a API com o Gemini
real é o da janela (pendente: precisa de chave e do stack no ar). Aqui estão a agregação (pura,
testada) e o ensaio.
"""

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config import SloTargets, load_slo_targets

QS = {"p50": 0.5, "p90": 0.9, "p95": 0.95, "p99": 0.99}
BAD = frozenset({"llm_unavailable", "budget_exceeded"})


@dataclass(frozen=True)
class Sample:
    student: str
    cls: str  # card | 1 | 2+
    total_s: float
    ttfe_s: float
    components_s: Mapping[str, float] = field(default_factory=dict)  # cascata por componente
    status: str = "ok"
    cost_usd: float = 0.0
    cached: int = 0
    input_tokens: int = 0


def _q(xs: Sequence[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, math.ceil(p * len(s)) - 1)]


def _ci(
    by_student: Mapping[str, list[float]], p: float, n_boot: int, rng: random.Random
) -> list[float]:
    """IC 95% por bootstrap de alunos (o aluno é a unidade; os turnos dele são correlacionados)."""
    ids = sorted(by_student)
    reps = []
    for _ in range(n_boot):
        pool = [x for s in rng.choices(ids, k=len(ids)) for x in by_student[s]]
        reps.append(_q(pool, p))
    return [_q(reps, 0.025), _q(reps, 0.975)]


def summarize(
    samples: Sequence[Sample], *, seed: int, n_boot: int = 2000, slo: SloTargets | None = None
) -> dict[str, Any]:
    slo = slo or load_slo_targets()
    rng = random.Random(seed)
    by_cls: dict[str, list[Sample]] = defaultdict(list)
    for x in samples:
        by_cls[x.cls].append(x)
    classes: dict[str, Any] = {}
    for cls, xs in by_cls.items():
        tgt = slo.targets[cls]
        by_student: dict[str, list[float]] = defaultdict(list)
        for x in xs:
            by_student[x.student].append(x.total_s)
        quant = {}
        for name, p in QS.items():
            v, target = _q([x.total_s for x in xs], p), getattr(tgt, name)
            quant[name] = {"value": v, "target": target, "ci": _ci(by_student, p, n_boot, rng),
                           "pass": v <= target}  # fmt: skip
        ttfe, t95 = _q([x.ttfe_s for x in xs], 0.95), slo.ttfe[cls].p95
        comps: dict[str, list[float]] = defaultdict(list)
        for x in xs:
            for k, v in x.components_s.items():
                comps[k].append(v)
        classes[cls] = {
            "n": len(xs),
            "quantiles": quant,
            "ttfe_p95": {"value": ttfe, "target": t95, "pass": ttfe <= t95},
            "cascade": {k: {"p50": _q(v, 0.5), "p95": _q(v, 0.95)} for k, v in comps.items()},
            "pass": all(q["pass"] for q in quant.values()) and ttfe <= t95,
        }  # fmt: skip
    n = len(samples)
    inp = sum(x.input_tokens for x in samples)
    return {
        "n": n,
        "classes": classes,
        "global_p95_s": _q([x.total_s for x in samples], 0.95),
        "share": {c: len(xs) / n for c, xs in by_cls.items()},  # peso de cada classe no p95 global
        "cache_ratio": sum(x.cached for x in samples) / inp if inp else 0.0,
        "cost_per_turn_usd": statistics.fmean(x.cost_usd for x in samples),
        "budget_exceeded_rate": sum(x.status == "budget_exceeded" for x in samples) / n,
        "bad_rate": sum(x.status in BAD for x in samples) / n,
    }


async def _sim_runner(per_class: int, seed: int, scale: float) -> list[Sample]:
    """Ensaio: cada turno usa k chamadas do LoadTestLLM (tempo simulado, sem dormir)."""
    from app.agent.fakes import LoadTestLLM

    slo = load_slo_targets()
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    llm = LoadTestLLM(slo.components, slo.quotas["gemini-3.8-flash"], seed=seed, sleep=sleep,
                      clock=lambda: 0.0)  # fmt: skip
    out = []
    for cls, k in {"card": 0, "1": 1, "2+": 2}.items():
        for i in range(per_class):
            tools = llm.sample("tool_call"), llm.sample("tool_call")
            calls = list(tools[:k])
            ans, ovh = llm.sample("answer_call"), llm.sample("overhead")
            total = (sum(calls) + ans + ovh) * scale
            comps = {"tool_call": sum(calls), "answer_call": ans, "overhead": ovh}
            out.append(
                Sample(f"s{i % 10}", cls, total, calls[0] * scale if calls else total, comps)
            )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench")
    ap.add_argument("--per-class", type=int, required=True)
    ap.add_argument("--window", choices=["A", "B", "C"], required=True)
    ap.add_argument("--model", default="gemini-3.8-flash")
    ap.add_argument("--runner", choices=["sim"], default="sim")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)
    samples = asyncio.run(_sim_runner(a.per_class, a.seed, 1.0))
    rep = summarize(samples, seed=a.seed)
    json.dump({"window": a.window, "model": a.model, "runner": a.runner, **rep}, sys.stdout,
              indent=2)  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
