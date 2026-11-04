"""Bancada de latência (spec §6.5): previsto × medido em todos os quantis, por classe.

    make eval-latency WINDOW=B N=100 MODEL=gemini-3.8-flash

Runner padrão (`http`): a API no perfil eval (EVAL_API_URL, iniciada com `LLM_MODEL=<--model>`);
um turno por vez, uma pergunta do dataset dev por classe de ferramentas; POST /chat em SSE;
TTFE = 1º `progress`/`answer`, total = `done`; classe, tempos e uso vêm do evento `debug`.
`--allow-sim --runner sim` é só ensaio do código (LoadTestLLM, sem rede): a saída leva
`"simulated": true` e NÃO é medição.
"""

import argparse
import asyncio
import json
import math
import os
import random
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
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
    model: str = ""


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
    if not samples:
        raise ValueError("sem amostras")
    slo = slo or load_slo_targets()
    rng = random.Random(seed)
    by_cls: dict[str, list[Sample]] = defaultdict(list)
    for x in samples:
        by_cls[x.cls].append(x)
    classes: dict[str, Any] = {c: {"n": 0, "pass": False} for c in slo.targets if c not in by_cls}
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


async def _sim_runner(per_class: int, seed: int) -> list[Sample]:
    """Ensaio: cada turno usa k chamadas do LoadTestLLM (tempo simulado, sem dormir)."""
    from app.agent.fakes import LoadTestLLM

    slo = load_slo_targets()

    async def sleep(s: float) -> None: ...

    llm = LoadTestLLM(slo.components, slo.quotas["gemini-3.8-flash"], seed=seed, sleep=sleep,
                      clock=lambda: 0.0)  # fmt: skip
    out = []
    for cls, k in {"card": 0, "1": 1, "2+": 2}.items():
        for i in range(per_class):
            calls = [llm.sample("tool_call") for _ in range(k)]
            ans, ovh = llm.sample("answer_call"), llm.sample("overhead")
            total = sum(calls) + ans + ovh
            comps = {"tool_call": sum(calls), "answer_call": ans, "overhead": ovh}
            out.append(Sample(f"s{i % 10}", cls, total, calls[0] if calls else total, comps))
    return out


# --------------------------------------------------------------------------- runner real (HTTP)
def class_of(n_tools: int) -> str:
    return "card" if n_tools == 0 else "1" if n_tools == 1 else "2+"


def pick_items(items: Sequence[Any], per_class: int) -> dict[str, list[Any]]:
    """`per_class` itens de turno único por classe (nº de ferramentas esperadas), alternando entre
    os alunos (a unidade do IC); com poucos itens distintos, repete (conversa nova a cada turno)."""
    pools: dict[str, dict[str, list[Any]]] = {"card": {}, "1": {}, "2+": {}}
    for it in items:
        if len(it.steps) == 1:
            pools[class_of(len(it.steps[0].expected_tools))].setdefault(
                it.id.rsplit("@", 1)[-1], []).append(it)  # fmt: skip
    out: dict[str, list[Any]] = {}
    for cls, by_student in pools.items():
        if not by_student:
            raise SystemExit(f"o dataset dev não tem item da classe {cls!r}")
        ring = [by_student[s] for s in sorted(by_student)]
        out[cls] = [ring[i % len(ring)][(i // len(ring)) % len(ring[i % len(ring)])]
                    for i in range(per_class)]  # fmt: skip
    return out


def parse_sse_line(buf: dict[str, Any], line: str) -> tuple[str, dict[str, Any]] | None:
    """Acumula as linhas de um evento SSE; devolve (evento, dados) na linha em branco."""
    if line.startswith("event:"):
        buf["ev"] = line[6:].strip()
    elif line.startswith("data:"):
        buf.setdefault("data", []).append(line[5:].strip())
    elif not line and buf.get("data"):
        ev, data = buf.get("ev", "message"), json.loads("\n".join(buf["data"]))
        buf.clear()
        return ev, data
    return None


async def ask_timed(
    http: Any, token: str, cid: str, text: str, *, student: str, on: date
) -> Sample | None:
    """Um turno cronometrado. None = admissão recusada (429/503): quem chama espera e repete."""
    t0 = time.perf_counter()
    ttfe = total = None
    debug: dict[str, Any] = {}
    status, buf = "error", {}
    async with http.stream("POST", "/chat", json={"conversation_id": cid, "text": text},
                           headers={"Cookie": f"session={token}",
                                    "Origin": str(http.base_url).rstrip("/")}) as r:  # fmt: skip
        if r.status_code in (429, 503):
            return None
        r.raise_for_status()
        async for line in r.aiter_lines():
            got = parse_sse_line(buf, line)
            if got is None:
                continue
            ev, d = got
            if ev in ("progress", "answer") and ttfe is None:
                ttfe = time.perf_counter() - t0
            elif ev == "debug":
                debug = d
            elif ev == "done":
                total, status = time.perf_counter() - t0, d.get("status", "ok")
    if total is None or not debug:
        raise RuntimeError("sem `debug`/`done`: a API precisa estar no perfil eval (APP_ENV=dev)")
    from app.agent.llm import Usage
    from app.obs.cost import cost_usd

    u = Usage(**debug["usage"])
    t = debug["timings_ms"]
    return Sample(
        student, debug["tools_class"], total, ttfe if ttfe is not None else total,
        {"llm": t["llm"] / 1000, "tools": t["tools"] / 1000, "guard": t["guard"] / 1000},
        status, float(cost_usd(u, debug["model"], on)), u.cached_tokens, u.input_tokens,
        debug["model"],
    )  # fmt: skip


async def http_runner(a: argparse.Namespace) -> list[Sample]:
    import httpx

    from app.config import AppSettings, load_slo_targets
    from core.clock import reference_today
    from evals import oracle as o
    from evals.golden.run import load_dataset
    from evals.preflight import project_round
    from evals.tokens import mint

    st, slo = AppSettings(), load_slo_targets()
    items, pks, _, _ = load_dataset("dev", raw=o.load_csv(a.data), hmac_key=st.hmac_key.encode(),
                                    holdout_yaml=None)  # fmt: skip
    plan = pick_items(items, a.per_class)
    proj = project_round(
        3 * a.per_class, slo.admission.est_requests, a.tokens_per_call, a.model,
        spend_today=a.spend_today, ceiling=Decimal(str(slo.limits["eval"].global_ceiling_usd)),
        rpd_left=a.rpd_left or slo.quotas[a.model].rpd,
    )  # fmt: skip
    print(
        f"preflight: ~US$ {proj.cost_usd:.2f}, {proj.calls} chamadas, ok={proj.ok}", file=sys.stderr
    )
    if not proj.ok:
        raise SystemExit(2)
    tokens = {s: mint(u, "chat") for s, u in pks.items()}
    out: list[Sample] = []
    async with httpx.AsyncClient(base_url=a.base_url, timeout=90.0) as http:
        for i in range(a.per_class):
            for cls in plan:  # intercala as classes: a deriva do provedor não vira viés de classe
                it = plan[cls][i]
                sid = it.id.rsplit("@", 1)[-1]
                for _ in range(10):
                    smp = await ask_timed(http, tokens[sid], os.urandom(8).hex(),
                                          it.steps[0].text, student=sid,
                                          on=reference_today(st))  # fmt: skip
                    if smp is not None:
                        break
                    await asyncio.sleep(2.0)  # admissão recusada: não é amostra
                else:
                    raise SystemExit("admissão não liberou o turno")
                if not out and not smp.model.startswith(a.model):
                    raise SystemExit(f"a API roda {smp.model!r}, não {a.model!r}: suba-a com "
                                     f"LLM_MODEL={a.model}")  # fmt: skip
                out.append(smp)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench")
    ap.add_argument("--per-class", type=int, required=True)
    ap.add_argument("--window", choices=["A", "B", "C"], required=True)
    ap.add_argument("--model", default="gemini-3.8-flash", help="deve ser o LLM_MODEL da API")
    ap.add_argument("--runner", choices=["http", "sim"], default="http")
    ap.add_argument("--allow-sim", action="store_true", help="libera o ensaio simulado")
    ap.add_argument("--base-url", default=os.environ.get("EVAL_API_URL", "http://localhost:8100"))
    ap.add_argument("--data", type=Path, default=Path("data/dados.zip"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--spend-today", type=Decimal, default=Decimal(0))
    ap.add_argument("--rpd-left", type=int)
    ap.add_argument("--tokens-per-call", type=int, default=3000)
    a = ap.parse_args(argv)
    if a.runner == "sim" and not a.allow_sim:
        print("o runner sim é ensaio, não medição: use --allow-sim", file=sys.stderr)
        return 2
    samples = (asyncio.run(_sim_runner(a.per_class, a.seed)) if a.runner == "sim"
               else asyncio.run(http_runner(a)))  # fmt: skip
    rep = {"window": a.window, "model": a.model, "runner": a.runner,
           "simulated": a.runner == "sim", **summarize(samples, seed=a.seed)}  # fmt: skip
    text = json.dumps(rep, indent=2)
    out = (
        a.out
        or Path(os.environ.get("EVAL_OUT_DIR") or tempfile.gettempdir())
        / f"latency-{a.window}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
