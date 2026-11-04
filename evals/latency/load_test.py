"""Escada (a) em processo: limitador por janela de 60 s + cota simulada, tempo simulado.

Ensaio da política de admissão sem gastar (LoadTestLLM: latências dos lognormais de
`slo_targets.components`). As escadas contra o stack real (limitador Redis, 1 e 2 workers) usam o
mesmo `run_ladder`/`ladder_a_pass`; critérios em docs/evidence/latency.md.
"""

import json
import random
import sys
from collections import deque

from app.config import load_slo_targets
from evals.latency.loadgen import Outcome, ladder_a_pass, run_ladder


def main(model: str = "gemini-3.8-flash", seed: int = 42) -> int:
    slo = load_slo_targets()
    q, adm = slo.quotas[model], slo.admission
    cap_req = int(q.rpm * adm.quota_fraction)
    window: deque[float] = deque()  # instantes das requisições ao LLM já reservadas

    def handler(t: float, rng: random.Random) -> Outcome:
        if window and t < window[-1]:  # o tempo recomeçou: novo degrau
            window.clear()
        while window and t - window[0] >= 60:
            window.popleft()
        if len(window) + adm.est_requests > cap_req:
            return Outcome(503, 0.0, retry_after_s=max(1.0, 60 - (t - window[0])))
        window.extend([t] * adm.est_requests)
        c = slo.components
        k = rng.choice([0, 1, 2, 2, 3])
        lat = sum(rng.lognormvariate(c["tool_call"].mu, c["tool_call"].sigma) for _ in range(k))
        lat += rng.lognormvariate(c["answer_call"].mu, c["answer_call"].sigma)
        lat += rng.lognormvariate(c["overhead"].mu, c["overhead"].sigma)
        return Outcome(200, lat)

    quota_tps = cap_req / 60 / adm.est_requests  # turnos/s que a cota sustenta
    rates = [quota_tps * f for f in (0.25, 0.5, 1.0, 1.5, 2.0)]  # o último é 2x a cota
    res = run_ladder(rates, 300, seed=seed, handler=handler)
    baseline = res.steps[0].p95
    ok = ladder_a_pass(res, baseline)
    json.dump({"pass": ok, "baseline_p95_s": baseline, "steps": [
        {"rate": s.rate, "sent": s.sent, "ok": s.ok, "rejected": s.rejected, "p95": s.p95}
        for s in res.steps]}, sys.stdout, indent=2)  # fmt: skip
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
