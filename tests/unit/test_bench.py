import random

import pytest

from app.config import load_slo_targets
from evals.latency.bench import Sample, summarize


def _samples(n: int, cls: str, total: float, ttfe: float) -> list[Sample]:
    rng = random.Random(1)
    return [
        Sample(f"s{i % 10}", cls, total * rng.uniform(0.9, 1.1), ttfe, {"answer_call": 2.0},
               status="ok", cost_usd=0.002, cached=100, input_tokens=1000)
        for i in range(n)
    ]  # fmt: skip


def test_summarize_checks_every_quantile_against_targets() -> None:
    slo = load_slo_targets()  # valores derivados das metas carregadas, não de números fixos
    tgt = {c: list(slo.targets[c].model_dump().values()) for c in ("card", "2+")}
    ok_total = min(tgt["card"]) * 0.8  # com jitter de até 1,1x, abaixo de todo p50..p99 de card
    xs = _samples(100, "card", ok_total, slo.ttfe["card"].p95 * 0.5)
    xs += _samples(100, "2+", max(tgt["2+"]) * 3, 3.0)
    rep = summarize(xs, seed=1, n_boot=200)
    assert rep["classes"]["card"]["pass"] is True
    assert rep["classes"]["2+"]["pass"] is False  # 3x a maior meta de 2+
    assert set(rep["classes"]["card"]["quantiles"]) == {"p50", "p90", "p95", "p99"}
    q = rep["classes"]["card"]["quantiles"]["p95"]
    assert q["ci"][0] <= q["value"] <= q["ci"][1] and q["target"] == slo.targets["card"].p95
    assert rep["classes"]["card"]["ttfe_p95"]["target"] == slo.ttfe["card"].p95
    assert rep["share"] == {"card": 0.5, "2+": 0.5}
    assert rep["cost_per_turn_usd"] == 0.002 and rep["budget_exceeded_rate"] == 0.0
    assert rep["cache_ratio"] == 0.1
    assert "answer_call" in rep["classes"]["card"]["cascade"]


def test_summarize_empty_class_and_empty_samples() -> None:
    import pytest

    rep = summarize(_samples(30, "card", 4.0, 4.0), seed=1, n_boot=50)
    assert rep["classes"]["2+"] == {"n": 0, "pass": False}  # classe sem amostra reprova
    with pytest.raises(ValueError):
        summarize([], seed=1)


def test_sim_runner_needs_allow_sim_and_is_marked(tmp_path, capsys) -> None:
    import json

    from evals.latency.bench import main

    args = [
        "--per-class",
        "10",
        "--window",
        "A",
        "--runner",
        "sim",
        "--out",
        str(tmp_path / "o.json"),
    ]
    assert main(args) == 2 and not (tmp_path / "o.json").exists()
    assert main([*args, "--allow-sim"]) == 0
    assert json.loads((tmp_path / "o.json").read_text())["simulated"] is True


def test_default_runner_is_http() -> None:
    import inspect

    from evals.latency import bench

    src = inspect.getsource(bench.main)
    assert 'default="http"' in src


def test_class_of_and_pick_items() -> None:
    from types import SimpleNamespace as NS

    from evals.latency.bench import class_of, pick_items

    assert [class_of(n) for n in (0, 1, 2, 5)] == ["card", "1", "2+", "2+"]

    def it(i: str, sid: str, n: int, steps: int = 1) -> NS:
        return NS(id=f"{i}@{sid}", steps=[NS(expected_tools=("t",) * n)] * steps)

    items = [it("a", "s1", 0), it("a", "s2", 0), it("b", "s1", 1), it("c", "s2", 3),
             it("m", "s1", 1, steps=2)]  # fmt: skip
    plan = pick_items(items, 4)
    assert {c: len(v) for c, v in plan.items()} == {"card": 4, "1": 4, "2+": 4}
    assert {x.id.rsplit("@")[-1] for x in plan["card"]} == {"s1", "s2"}  # alterna os alunos
    assert all(len(x.steps) == 1 for v in plan.values() for x in v)  # multi-turno fica de fora


async def test_ask_timed_reads_sse_debug_and_ttfe() -> None:
    import json
    from datetime import date

    import httpx

    from evals.latency.bench import ask_timed

    dbg = {"tools_class": "1", "model": "gemini-3.8-flash", "timings_ms": {
        "total": 5, "llm": 3000, "tools": 100, "guard": 5},
        "llm_calls_s": [["tool_call", 1.0], ["answer_call", 2.0]],
        "usage": {"input_tokens": 1000, "output_tokens": 100, "thinking_tokens": 0,
                  "cached_tokens": 200}}  # fmt: skip

    def ev(n: str, d: dict) -> str:
        return f"event: {n}\ndata: {json.dumps(d)}\n\n"

    body = (ev("meta", {}) + ev("progress", {}) + ev("answer", {"text": "x"}) + ev("debug", dbg)
            + ev("done", {"status": "ok"}))  # fmt: skip

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body.encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://t") as h:
        s = await ask_timed(h, "tok", "c", "oi", student="s1", on=date(2026, 10, 31))
    assert s is not None and s.cls == "1" and s.status == "ok" and s.model == "gemini-3.8-flash"
    assert 0 <= s.ttfe_s <= s.total_s and s.cached == 200 and s.input_tokens == 1000
    assert s.cost_usd > 0
    assert s.llm_calls_s == (("tool_call", 1.0), ("answer_call", 2.0))
    c = s.components_s
    assert c["tool_call"] == 1.0 and c["answer_call"] == 2.0
    assert c["overhead"] >= 0 and c["overhead"] == pytest.approx(max(s.total_s - 3.0, 0.0))

    def rej(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "3"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(rej), base_url="http://t") as h:
        assert await ask_timed(h, "tok", "c", "oi", student="s1", on=date(2026, 10, 31)) is None


def test_served_model_must_equal_the_requested_one_unless_allow_sim() -> None:
    import pytest

    from evals.latency.bench import check_served_model

    assert check_served_model("gemini-3.8-flash", "gemini-3.8-flash", allow_sim=False) is False
    for bad in ("loadtest", "demo-replay", ""):
        with pytest.raises(SystemExit, match="--allow-sim"):
            check_served_model(bad, "gemini-3.8-flash", allow_sim=False)
        assert check_served_model(bad, "gemini-3.8-flash", allow_sim=True) is True  # simulated


def test_samples_jsonl_next_to_summary_without_text(tmp_path) -> None:
    import json

    from evals.latency.bench import main

    out = tmp_path / "latency-A.json"
    assert (
        main(
            [
                "--per-class",
                "4",
                "--window",
                "A",
                "--runner",
                "sim",
                "--allow-sim",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    rows = [json.loads(x) for x in (tmp_path / "latency-A-samples.jsonl").read_text().splitlines()]
    assert len(rows) == 12
    keys = {"student", "cls", "status", "total_s", "ttfe_s", "llm_calls_s", "overhead_s", "model"}
    assert all(set(r) == keys for r in rows)
    assert {r["cls"] for r in rows} == {"card", "1", "2+"}
    assert [k for k, _ in next(r for r in rows if r["cls"] == "2+")["llm_calls_s"]] == [
        "tool_call",
        "tool_call",
        "answer_call",
    ]
