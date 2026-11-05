import json
from pathlib import Path

import httpx
import yaml

from app.config import load_slo_targets
from evals.latency.load_test import (
    counter,
    derive_quota,
    one_turn,
    quota_turns_per_s,
    reasons_delta,
    run_ladder_http,
    write_unlimited,
)

SLO = load_slo_targets()


def test_quota_turns_per_s_from_yaml() -> None:
    est = SLO.admission.est_requests
    assert quota_turns_per_s(SLO, "gemini-3.8-flash") == 1000 * 0.9 / 60 / est


def test_derive_quota_binds_below_slot_capacity() -> None:
    d = derive_quota(SLO, "gemini-3.8-flash")
    assert d["quota_turns_per_s"] <= 0.7 * d["slot_turns_per_s"] + 1e-9
    assert d["slot_turns_per_s"] == SLO.turn_slots / d["mean_turn_s"]
    assert d["mean_turn_s"] > d["p50_turn_s"]  # a cauda puxa a média
    q = d["quota"]  # o rpm simulado reproduz a capacidade em turnos/s pela mesma conta do balde
    assert q["rpm"] * SLO.admission.quota_fraction / 60 / SLO.admission.est_requests == (
        __import__("pytest").approx(d["quota_turns_per_s"], rel=0.02)
    )
    assert q["tpm"] > 100 * q["rpm"]  # o RPM é quem limita


def test_write_slo_for_ladder_a_applies_derived_quota(tmp_path: Path) -> None:
    from evals.latency.load_test import write_slo

    out = tmp_path / "a.yaml"
    write_slo(Path("slo_targets.yaml"), out, "a", "gemini-3.8-flash")
    got = load_slo_targets(out)
    assert (
        got.quotas["gemini-3.8-flash"].rpm == derive_quota(SLO, "gemini-3.8-flash")["quota"]["rpm"]
    )
    assert got.turn_slots == SLO.turn_slots  # as vagas não mudam na escada (a)


def test_reasons_delta_parses_labeled_counter() -> None:
    a = 'agent_admission_rejected_total{reason="quota"} 2.0\n'
    b = (
        'agent_admission_rejected_total{reason="quota"} 5.0\n'
        'agent_admission_rejected_total{reason="slots"} 3.0\n'
    )
    assert reasons_delta(a, b) == {"quota": 3, "slots": 3}


def test_write_unlimited_keeps_everything_else(tmp_path: Path) -> None:
    out = tmp_path / "u.yaml"
    write_unlimited(Path("slo_targets.yaml"), out)
    d = yaml.safe_load(out.read_text())
    assert d["admission"]["quota_fraction"] == 1000.0 and d["turn_slots"] == 10**6
    assert d["components"] == yaml.safe_load(Path("slo_targets.yaml").read_text())["components"]
    assert load_slo_targets(out).admission.est_requests == SLO.admission.est_requests


def test_counter_sums_series() -> None:
    t = "agent_llm_retries_total 4.0\nagent_llm_seconds_count 10.0\nagent_llm_seconds_sum 3.0\n"
    assert counter(t, "agent_llm_retries") == 4 and counter(t, "agent_llm_seconds") == 10


def _api(admit_every: int = 2, bad: bool = False):
    n = {"i": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/metrics":
            c = n["i"]
            return httpx.Response(
                200, text=f"agent_llm_seconds_count {c * 3}\nagent_llm_retries_total {c}\n"
            )
        n["i"] += 1
        if n["i"] % admit_every:
            return httpx.Response(503, headers={"Retry-After": "2"})
        st = "budget_exceeded" if bad else "ok"
        return httpx.Response(
            200, content=f"event: done\ndata: {json.dumps({'status': st})}\n\n".encode()
        )

    return handler


async def test_one_turn_outcomes() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(_api(1, bad=True)),
                                 base_url="http://t") as h:  # fmt: skip
        o = await one_turn(h, "t", "oi")
        assert o["rej"] is False and o["cut"] is True
    async with httpx.AsyncClient(transport=httpx.MockTransport(_api(5)), base_url="http://t") as h:
        o = await one_turn(h, "t", "oi")
        assert o["rej"] is True and o["retry_after"] == "2"


async def test_run_ladder_http_records_rejections_retries_and_cuts(monkeypatch) -> None:
    transport = httpx.MockTransport(_api(2, bad=True))
    orig = httpx.AsyncClient

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: orig(**{**kw, "transport": transport}))
    res = await run_ladder_http("http://t", ["tok"], [200.0, 400.0], 0.3, seed=1)
    s = res.steps[-1]
    assert s.sent > 20 and s.rejected > 0 and s.ok > 0
    assert s.rejected_no_retry_after == 0  # todo 503 leva Retry-After
    assert s.cut == s.ok  # tudo que foi admitido terminou em budget_exceeded
    assert s.retries > 0  # delta de /metrics, não zero por construção
