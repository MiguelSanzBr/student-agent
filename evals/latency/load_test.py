"""Escadas de carga contra a API (laço aberto, Poisson): `make load-test`.

A API roda com LLM_PROVIDER=loadtest (LoadTestLLM: latências dos lognormais do slo_targets, sem
gastar), Redis, `AdmissionBucket` e `TurnSlots` reais, perfil de limites "load"
(ops/compose.load.yml).
Escada (a): degraus de 0,25× a 2× a cota, com limitador; avaliada por `ladder_a_pass`.
Escada (b): sem limitador e com vagas ilimitadas (`--write-unlimited` gera o yaml), descritiva;
1 vs 2 workers por UVICORN_WORKERS. O driver registra, por degrau, admitidos, 503/429 (e se levam
`Retry-After`), cortados (status ruim, erro SSE, timeout) e retries do LLM (delta de /metrics).
"""

import argparse
import asyncio
import json
import math
import os
import random
import re
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import yaml

from app.agent.fakes import MAX_TOOLS, P_TOOL
from app.config import SloTargets, load_slo_targets
from evals.latency.loadgen import LadderResult, StepResult, ladder_a_pass

# 0,7 já deixava as vagas recusarem a 1× a cota (chegadas de Poisson: com ~5,6 turnos em voo de
# 8 vagas, P(≥ 8) ≈ 20%); a 0,4 são ~3 em voo e o limitador de cota domina as recusas.
SLOT_SHARE = 0.4
CUT = frozenset({"budget_exceeded", "llm_unavailable", "error"})
LADDER_A_FACTORS = (0.25, 0.5, 1.0, 1.5, 2.0)  # o último = 2× a cota


def quota_turns_per_s(slo: SloTargets, model: str) -> float:
    """Turnos/s que a cota sustenta no balde: fração × RPM ÷ chamadas estimadas por turno."""
    return slo.quotas[model].rpm * slo.admission.quota_fraction / 60 / slo.admission.est_requests


def derive_quota(slo: SloTargets, model: str, *, seed: int = 0, n: int = 20_000) -> dict[str, Any]:
    """Cota simulada da escada (a): turnos/s da cota ≤ `SLOT_SHARE` (0,4) × a das vagas, para que
    o limitador de cota, e não as vagas, seja o gargalo. Vagas ÷ **média** do turno (Little:
    as vagas seguram o turno inteiro, e a cauda lognormal puxa a média acima do p50; com o p50
    as vagas recusaram já a 1× a cota, smoke de 2026-11-04). O turno vem do sorteio do LoadTestLLM
    (k ferramentas com `P_TOOL`, no máximo `MAX_TOOLS`) mais o overhead do componente."""
    rng = random.Random(seed)
    c, adm = slo.components, slo.admission

    def turn() -> float:
        k = 0
        while k < MAX_TOOLS and rng.random() < P_TOOL:
            k += 1
        t = sum(rng.lognormvariate(c["tool_call"].mu, c["tool_call"].sigma) for _ in range(k))
        t += rng.lognormvariate(c["answer_call"].mu, c["answer_call"].sigma)
        return t + rng.lognormvariate(c["overhead"].mu, c["overhead"].sigma)

    xs = sorted(turn() for _ in range(n))
    p50, mean = xs[n // 2], sum(xs) / n
    slot_tps = slo.turn_slots / mean
    rpm = math.floor(SLOT_SHARE * slot_tps * 60 * adm.est_requests / adm.quota_fraction)
    quota = {"rpm": rpm, "tpm": rpm * adm.est_tokens, "rpd": slo.quotas[model].rpd}
    return {
        "p50_turn_s": p50, "mean_turn_s": mean, "turn_slots": slo.turn_slots,
        "slot_turns_per_s": slot_tps,
        "rule": f"quota_turns_per_s <= {SLOT_SHARE} * turn_slots / mean_turn_s",
        "quota": quota, "quota_turns_per_s": rpm * adm.quota_fraction / 60 / adm.est_requests,
    }  # fmt: skip


def write_slo(slo_path: Path, out: Path, ladder: str, model: str) -> None:
    """yaml do `SLO_TARGETS_PATH` da API: (a) cota simulada derivada; (b) sem limitador."""
    if ladder == "b":
        return write_unlimited(slo_path, out)
    d = yaml.safe_load(slo_path.read_text())
    d["quotas"][model] = derive_quota(load_slo_targets(slo_path), model)["quota"]
    out.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))


def reasons_delta(m0: str, m1: str) -> dict[str, int]:
    """Recusas por motivo entre duas leituras de /metrics (agent_admission_rejected_total)."""
    rx = re.compile(r'^agent_admission_rejected_total\{reason="(\w+)"\} ([0-9.e+-]+)$', re.M)
    a, b = dict(rx.findall(m0)), dict(rx.findall(m1))
    return {k: round(float(v) - float(a.get(k, 0))) for k, v in b.items()
            if float(v) > float(a.get(k, 0))}  # fmt: skip


def write_unlimited(slo_path: Path, out: Path) -> None:
    """Escada (b): quota_fraction e vagas enormes (a admissão nunca recusa)."""
    d = yaml.safe_load(slo_path.read_text())
    d["admission"]["quota_fraction"] = 1000.0
    d["turn_slots"] = 10**6
    out.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))


def counter(metrics_text: str, name: str) -> float:
    """Soma das séries de um contador/histograma `name` (somando `_total` ou `_count`)."""
    rx = re.compile(rf"^{name}(?:_total|_count)?(?:\{{[^}}]*\}})? ([0-9.e+-]+)$", re.M)
    return sum(float(m) for m in rx.findall(metrics_text))


async def one_turn(http: httpx.AsyncClient, token: str, text: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        async with http.stream("POST", "/chat", json={"conversation_id": os.urandom(8).hex(),
                                                      "text": text},
                               headers={"Cookie": f"session={token}",
                                        "Origin": str(http.base_url).rstrip("/")},
                               ) as r:  # fmt: skip
            if r.status_code in (429, 503):
                return {"rej": True, "retry_after": r.headers.get("Retry-After")}
            r.raise_for_status()
            status, ev = "error", ""
            async for line in r.aiter_lines():
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:") and ev == "done":
                    status = json.loads(line[5:]).get("status", "ok")
                elif line.startswith("data:") and ev == "error":
                    status = "error"
            return {"rej": False, "lat": time.perf_counter() - t0, "cut": status in CUT}
    except (httpx.HTTPError, OSError):
        return {"rej": False, "lat": time.perf_counter() - t0, "cut": True}  # transporte: cortado


async def run_step(
    http: httpx.AsyncClient,
    tokens: Sequence[str],
    rate: float,
    duration_s: float,
    rng: random.Random,
) -> StepResult:
    m0 = (await http.get("/metrics")).text
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    end, t = time.perf_counter() + duration_s, time.perf_counter()
    while t < end:  # laço aberto: não espera a resposta antes da próxima chegada
        t += rng.expovariate(rate)
        await asyncio.sleep(max(0.0, t - time.perf_counter()))
        tasks.append(asyncio.create_task(one_turn(http, rng.choice(tokens), "Qual o meu saldo?")))
    out = await asyncio.gather(*tasks)
    m1 = (await http.get("/metrics")).text
    llm = counter(m1, "agent_llm_seconds") - counter(m0, "agent_llm_seconds")
    retries = counter(m1, "agent_llm_retries") - counter(m0, "agent_llm_retries")
    rej = [o for o in out if o["rej"]]
    ok = [o for o in out if not o["rej"]]
    sent = len(out)
    return StepResult(
        rate, sent, len(ok), len(rej), sum(o["retry_after"] is None for o in rej),
        round(retries / llm * sent) if llm else 0,  # fração de chamadas com retry, escala `sent`
        sum(o["cut"] for o in ok), [o["lat"] for o in ok], reasons_delta(m0, m1),
    )  # fmt: skip


async def run_ladder_http(
    base_url: str, tokens: Sequence[str], rates: Sequence[float], duration_s: float, *, seed: int
) -> LadderResult:
    rng = random.Random(seed)
    limits = httpx.Limits(max_connections=None, max_keepalive_connections=None)
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0, limits=limits) as http:
        return LadderResult([await run_step(http, tokens, r, duration_s, rng) for r in rates])


def student_tokens(login_json: Path) -> list[str]:
    from evals.tokens import mint

    cases = json.loads(login_json.read_text())["cases"]
    return [mint(UUID(str(c["person_key"])), "chat") for c in cases]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="load-test")
    ap.add_argument("--base-url", default=os.environ.get("EVAL_API_URL", "http://localhost:8013"))
    ap.add_argument("--ladder", choices=["a", "b"], default="a")
    ap.add_argument(
        "--workers", type=int, default=1, help="só rotula o resultado (UVICORN_WORKERS)"
    )
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--factors", help="degraus como múltiplos da cota, ex.: 0.25 (ensaio curto)")
    ap.add_argument("--duration", type=float, default=60.0, help="segundos por degrau")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--login-json",
        type=Path,
        default=Path(os.environ.get("ETL_OUT_DIR", "/etl-out")) / "golden_login.json",
    )
    ap.add_argument("--write-slo", type=Path, help="gera o yaml da escada (--ladder) e sai")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.write_slo:
        write_slo(Path("slo_targets.yaml"), a.write_slo, a.ladder, a.model)
        return 0
    slo = load_slo_targets()
    derivation = derive_quota(slo, a.model) if a.ladder == "a" else None
    qt = derivation["quota_turns_per_s"] if derivation else quota_turns_per_s(slo, a.model)
    factors = (
        [float(x) for x in a.factors.split(",")] if a.factors
        else LADDER_A_FACTORS if a.ladder == "a" else (0.5, 1.0, 2.0, 4.0, 8.0)
    )  # fmt: skip
    rates = [qt * f for f in factors]
    res = asyncio.run(run_ladder_http(a.base_url, student_tokens(a.login_json), rates,
                                      a.duration, seed=a.seed))  # fmt: skip
    base = res.steps[0].p95
    rep: dict[str, Any] = {
        "ladder": a.ladder, "workers": a.workers, "simulated": False, "quota_turns_per_s": qt,
        "derivation": derivation,
        "baseline_p95_s": base,
        "steps": [{"rate": s.rate, "sent": s.sent, "ok": s.ok, "rejected": s.rejected,
                   "rejected_no_retry_after": s.rejected_no_retry_after, "retries": s.retries,
                   "cut": s.cut, "p95": s.p95, "reasons": dict(s.reasons)} for s in res.steps],
    }  # fmt: skip
    if a.ladder == "a":
        rep["pass"] = ladder_a_pass(res, base)
    text = json.dumps(rep, indent=2)
    out = (
        a.out
        or Path(os.environ.get("EVAL_OUT_DIR") or tempfile.gettempdir())
        / f"load-{a.ladder}-w{a.workers}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(text)
    return 0 if rep.get("pass", True) else 1


if __name__ == "__main__":
    sys.exit(main())
