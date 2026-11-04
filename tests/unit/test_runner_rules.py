import json
from collections import Counter
from pathlib import Path

import httpx

from evals.client import Rejected
from evals.golden.run import (
    append_round,
    run_round,
    sample_scorer_validation,
    sample_utility,
)
from evals.golden.score import Check, DatasetItem, TurnRecord
from evals.stats import ItemResult

ITEM = DatasetItem("t1@s1", "t1", "academico", "shared", (Check("q", required=("ativa",)),))
GOOD = TurnRecord("Está ativa.", trace_id="tr")


def http_err(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/chat")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


class FakeClient:
    """Responde em ordem; Exception é levantada, TurnRecord é devolvido."""

    def __init__(self, *script: object) -> None:
        self.script, self.calls = list(script), []
        self.last_meta: dict[str, str] = {}

    async def ask(self, token: str, cid: str, text: str) -> TurnRecord:
        self.calls.append(cid)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step  # type: ignore[return-value]


async def go(client, items=(ITEM,), **kw):
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    r = await run_round(list(items), {"s1": "tok"}, client, sleep=sleep, **kw)
    return r, slept


async def test_429_redone_once_then_ok():
    r, _ = await go(FakeClient(http_err(429), GOOD))
    (o,) = r.outcomes
    assert o.status == "pass" and o.attempts == 2 and r.redone == 1 and r.invalid == 0


async def test_second_429_is_invalid_and_out_of_denominator():
    r, _ = await go(FakeClient(http_err(429), http_err(503 + 0)))
    (o,) = r.outcomes
    assert o.status == "invalid" and r.invalid == 1 and r.valid == []
    r, _ = await go(FakeClient(http_err(500), http_err(502)))
    assert r.invalid == 1


async def test_cost_degraded_turn_invalid_and_redone():
    deg = TurnRecord("Está ativa.", cost_degraded=True)
    r, _ = await go(FakeClient(deg, GOOD))
    assert r.outcomes[0].status == "pass" and r.redone == 1
    r, _ = await go(FakeClient(deg, deg))
    assert r.outcomes[0].status == "invalid"


async def test_admission_rejection_waits_without_counting_attempt():
    c = FakeClient(Rejected(429, 2.0), Rejected(503, 3.0), GOOD)
    r, slept = await go(c)
    (o,) = r.outcomes
    assert slept == [2.0, 3.0] and o.attempts == 1 and r.redone == 0 and o.status == "pass"


async def test_graph_budget_exceeded_is_a_failure_in_denominator():
    r, _ = await go(FakeClient(TurnRecord("", status="budget_exceeded")))
    assert r.outcomes[0].status == "fail" and len(r.valid) == 1


async def test_hard_ceiling_aborts_round_and_is_counted(tmp_path):
    two = [ITEM, DatasetItem("t2@s1", "t2", "academico", "shared", ITEM.steps)]
    r, _ = await go(FakeClient(GOOD, Rejected(503, 300.0)), items=two)
    assert r.aborted and len(r.outcomes) == 1
    path = tmp_path / "rounds.jsonl"
    append_round(path, {"split": "test", "round": 1, "sha": "abc", "aborted": r.aborted})
    append_round(path, {"split": "test", "round": 2, "sha": "abc", "aborted": False})
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["aborted"] for x in rows] == [True, False]


async def test_failure_gets_a_cause():
    r, _ = await go(FakeClient(TurnRecord("Está trancada.", ())))
    assert r.outcomes[0].status == "fail" and r.outcomes[0].cause == "redacao"


def results() -> list[ItemResult]:
    cats = ["a", "b", "c"]
    return [ItemResult(f"i{n:03d}", f"s{n % 5}", f"t{n}", n % 5 != 0, cats[n % 3])
            for n in range(150)]  # fmt: skip


def test_scorer_validation_sample_24_6():
    res = results()
    s = sample_scorer_validation(res, seed=20261104)
    assert len(s) == 30 and sum(x.passed for x in s) == 24
    assert all(x.category for x in s)
    assert s == sample_scorer_validation(list(reversed(res)), seed=20261104)  # ordena por item_id
    by = Counter(x.category for x in s if x.passed)
    assert max(by.values()) - min(by.values()) <= 1  # proporcional (categorias iguais)


def test_scorer_validation_few_failures_takes_all():
    res = [ItemResult(f"i{n}", "s", f"t{n}", n > 2, "a") for n in range(60)]
    s = sample_scorer_validation(res, seed=1)
    assert len(s) == 30 and sum(not x.passed for x in s) == 3


def test_sample_utility_deterministic():
    convs = [f"c{n}" for n in range(100)]
    assert sample_utility(convs, seed=3) == sample_utility(convs, seed=3)
    assert len(sample_utility(convs, 30, seed=3)) == 30


def test_summarize_and_render_smoke():
    from evals.golden.run import Outcome, RoundResult, render_md, summarize

    outs = [
        Outcome(f"t{n}@s{n % 4}", f"s{n % 4}", f"t{n}", "academico" if n % 2 else "cruzamento",
                "shared" if n % 3 else "test_only", "pass" if n % 5 else "fail", 1,
                cause=None if n % 5 else "redacao")
        for n in range(40)
    ]  # fmt: skip
    rr = RoundResult(outs, redone=1)
    s = summarize(rr, strata={f"s{i}": "x" for i in range(4)}, n_boot=50,
                  meta={"split": "dev", "round": 1, "sha": "abc", "model": "m"})  # fmt: skip
    assert s["primary"]["n"] == 40 and s["by_cause"] == {"redacao": 8}
    md = render_md(s)
    assert "Primário" in md and "pendente" in md


def test_pick_enrollment_and_paraphrase_rules():
    from dataclasses import replace

    from evals.golden.run import apply_paraphrases, pick_enrollment

    st = {("B", "2"): "ACTIVE", ("A", "9"): "CANCELLED", ("C", "1"): "ACTIVE"}
    assert pick_enrollment(list(st), st, True) == ("B", "2")
    assert pick_enrollment(list(st), st, False) == ("A", "9")
    out = apply_paraphrases([ITEM], [{"id": "t1", "paraphrases": ["p0", "p1"]}])
    assert out[0].steps[0].text in ("p0", "p1") and out[0].steps[0] != ITEM.steps[0]
    assert apply_paraphrases([ITEM], [{"id": "t1"}]) == [ITEM]
    assert replace(ITEM) == ITEM


async def test_runner_end_to_end_with_oracle_ideal_answers(tmp_path):
    """Itens reais do dataset (fixture mini) + respostas ideais do oráculo: tudo aprova."""
    from evals.golden.build import build_items, ideal_turns, load_shared
    from evals.golden.run import _transcripts, summarize
    from evals.golden.select_conversations import select
    from evals.oracle import load_csv

    raw = load_csv(Path(__file__).parents[1] / "fixtures" / "mini")
    enr = {"s1": min((r["BRAND"], r["STUDENT_ID"]) for r in raw["sag/enrollment"])}
    items = build_items(load_shared(), [], enr, raw)
    ideal = {i.id: ideal_turns(i, raw)[0] for i in items}

    class Ideal(FakeClient):
        async def ask(self, token, cid, text):
            self.calls.append(text)
            it = next(i for i in items if i.steps[0].text == text and i.id not in self.done)
            self.done.add(it.id)
            return ideal[it.id]

    c = Ideal()
    c.done = set()
    r, _ = await go(c, items=items)
    assert r.valid and all(o.status == "pass" for o in r.valid), [
        (o.item_id, o.reasons) for o in r.valid if o.status != "pass"
    ]
    s = summarize(r, strata={"s1": "x"}, n_boot=20, meta={"split": "dev"})
    assert s["primary"]["point"] == 1.0
    (tmp_path / "transcripts.jsonl").write_text(_transcripts(r, {i.id: i for i in items}))
    assert len(select(tmp_path)) == min(30, len(items))
    assert (tmp_path / "conversations.md").exists() and (tmp_path / "utility_labels.csv").exists()
