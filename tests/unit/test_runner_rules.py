import json
from collections import Counter
from pathlib import Path

import httpx
import pytest

from evals.client import Rejected
from evals.golden.run import (
    Outcome,
    RoundExists,
    RoundResult,
    StateError,
    _ci,
    claim_round,
    close_round,
    code_state,
    frozen_sha,
    is_primary,
    parse,
    resummarize,
    round_states,
    run_round,
    sample_scorer_validation,
    sample_utility,
    summarize,
    validate_args,
    write_private,
)
from evals.golden.score import Check, DatasetItem, TurnRecord
from evals.stats import ItemResult

ITEM = DatasetItem("t1@s1", "t1", "academico", "shared", (Check("q", required=("ativa",)),))
GOOD = TurnRecord("Está ativa.", trace_id="tr")


def http_err(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://x/chat")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


class FakeClient:
    """Responde em ordem; Exception é levantada, TurnRecord é devolvido, (TurnRecord, meta)
    também preenche `last_meta` (modelo servido, tool_values)."""

    def __init__(self, *script: object) -> None:
        self.script, self.calls = list(script), []
        self.last_meta: dict[str, object] = {}

    async def ask(self, token: str, cid: str, text: str) -> TurnRecord:
        self.calls.append(cid)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        if isinstance(step, tuple):
            step, self.last_meta = step
        return step  # type: ignore[return-value]


async def go(client, items=(ITEM,), **kw):
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    r = await run_round(list(items), {"s1": "tok"}, client, sleep=sleep, **kw)
    return r, slept


async def test_provider_failure_redone_once_then_ok():
    # 429 do provedor chega como evento `error` do stream; 5xx como HTTPStatusError
    r, _ = await go(FakeClient(TurnRecord("", status="error"), GOOD))
    (o,) = r.outcomes
    assert o.status == "pass" and o.attempts == 2 and r.redone == 1 and r.invalid == 0
    r, _ = await go(FakeClient(http_err(502), GOOD))
    assert r.outcomes[0].status == "pass" and r.redone == 1


async def test_second_provider_failure_is_invalid_and_out_of_denominator():
    r, _ = await go(FakeClient(TurnRecord("", status="llm_unavailable"), http_err(500)))
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
    claim_round(path, {"split": "test", "round": 1, "sha": "abc"})
    close_round(path, {"split": "test", "round": 1, "sha": "abc", "aborted": r.aborted})
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["status"] for x in rows] == ["started", "aborted"]
    with pytest.raises(RoundExists):  # a rodada do teste é contada, mesmo a abortada
        claim_round(path, {"split": "test", "round": 1, "sha": "abc"})
    claim_round(path, {"split": "dev", "round": 1, "sha": "abc"})


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
    from evals.golden.run import apply_paraphrases, pick_enrollment

    st = {("B", "2"): "ACTIVE", ("A", "9"): "CANCELLED", ("C", "1"): "ACTIVE"}
    assert pick_enrollment(list(st), st, True) == ("B", "2")
    assert pick_enrollment(list(st), st, False) == ("A", "9")
    out = apply_paraphrases([ITEM], [{"id": "t1", "paraphrases": ["p0", "p1"]}])
    assert out[0].steps[0].text in ("p0", "p1") and out[0].steps[0] != ITEM.steps[0]
    assert apply_paraphrases([ITEM], [{"id": "t1"}]) == [ITEM]


async def test_runner_end_to_end_with_oracle_ideal_answers(tmp_path):
    """Itens reais do dataset (fixture mini) + respostas ideais do oráculo: tudo aprova."""
    from evals.golden.build import build_items, ideal_turns, load_shared
    from evals.golden.run import summarize, write_private
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
    write_private(tmp_path, r, {i.id: i for i in items}, {"s1": "x"})
    assert len(select(tmp_path)) == min(30, len(items))
    assert (tmp_path / "conversations.md").exists() and (tmp_path / "utility_labels.csv").exists()


async def test_unexpected_exception_aborts_round_keeping_partial_results():
    two = [ITEM, DatasetItem("t2@s1", "t2", "academico", "shared", ITEM.steps)]
    r, _ = await go(FakeClient(GOOD, RuntimeError("boom")), items=two)
    assert r.aborted and "RuntimeError" in r.abort_reason and len(r.outcomes) == 1


async def test_first_turn_model_mismatch_aborts():
    ok = (GOOD, {"model": "m1"})
    r, _ = await go(FakeClient(ok), expect_model="m1")
    assert not r.aborted and r.served_models == {"m1": 1}
    r, _ = await go(FakeClient((GOOD, {"model": "m2"})), expect_model="m1")
    assert r.aborted and "m2" in r.abort_reason
    r, _ = await go(FakeClient(GOOD), expect_model="m1")  # done.model ausente também reprova
    assert r.aborted and "ausente" in r.abort_reason


async def test_tokens_minted_lazily_per_student():
    minted: list[str] = []

    def tok(s: str) -> str:
        minted.append(s)
        return "t"

    two = [ITEM, DatasetItem("t2@s1", "t2", "academico", "shared", ITEM.steps),
           DatasetItem("t1@s2", "t1", "academico", "shared", ITEM.steps)]  # fmt: skip
    await run_round(two, tok, FakeClient(GOOD, GOOD, GOOD), sleep=lambda s: None)  # type: ignore[arg-type]
    assert minted == ["s1", "s2"]


async def test_dados_cause_from_tool_values():
    it = DatasetItem("t1@s1", "t1", "c", "shared",
                     (Check("q", expected_tools=("get_x",), required=("ativa",)),))  # fmt: skip
    bad = TurnRecord("Está trancada.", ("get_x",))
    r, _ = await go(FakeClient((bad, {"tool_values": {"get_x": ["TRANCADA"]}})), items=[it])
    assert r.outcomes[0].cause == "dados"
    r, _ = await go(FakeClient((bad, {"tool_values": {"get_x": ["ativa"]}})), items=[it])
    assert r.outcomes[0].cause == "redacao"


def test_ci_raises_for_primary_when_it_cannot_be_computed():
    o = Outcome("a@s", "s", "t", "x", "shared", "pass", 1)
    with pytest.raises(Exception):  # noqa: B017  - sem templates suficientes: não engole
        _ci([o], {}, 10, strict=True)  # estrato do aluno ausente => KeyError


# ----------------------------------------------------------------------------- política do teste
BASE = ["--split", "test", "--model", "m", "--round", "1"]
OK_TEST = [*BASE, "--record", "--dev-json", "dev.json", "--spend-today", "1", "--rpd-left", "9",
           "--out", "OUT"]  # fmt: skip


def check(argv, tmp_path):
    argv = [str(tmp_path) if x == "OUT" else x for x in argv]
    return validate_args(parse(argv))


def test_test_split_policy(tmp_path):
    assert check(OK_TEST, tmp_path) is None
    assert "--record" in check([x for x in OK_TEST if x != "--record"], tmp_path)
    no_dev = OK_TEST.copy()
    i = no_dev.index("--dev-json")
    del no_dev[i : i + 2]
    assert "--dev-json" in check(no_dev, tmp_path)
    for extra in (["--allow-dirty"], ["--paraphrase"], ["--n-boot", "100"], ["--rehearsal"]):
        assert check([*OK_TEST, *extra], tmp_path), extra  # no teste não há ensaio nem ajuste
    i = OK_TEST.index("--spend-today")
    assert "--spend-today" in check(OK_TEST[:i] + OK_TEST[i + 2 :], tmp_path)
    assert "repositório" in check([*OK_TEST[:-1], str(Path("docs/evidence/x").resolve())], tmp_path)
    # o ensaio e o ajuste são do dev
    dev = ["--split", "dev", "--model", "m", "--round", "1"]
    assert validate_args(parse(dev)) is None
    assert validate_args(parse([*dev, "--rehearsal", "--paraphrase", "--allow-dirty"])) is None
    assert "--rehearsal" in validate_args(parse([*dev, "--limit", "3"]))
    assert validate_args(parse([*dev, "--limit", "3", "--rehearsal"])) is None


def test_out_refused_under_evidence_dirs(tmp_path, monkeypatch):
    ev = tmp_path / "evid"
    monkeypatch.setenv("EVIDENCE_DIR", str(ev))
    assert "repositório" in check([*OK_TEST[:-1], str(ev / "q")], tmp_path)
    assert "repositório" in check([*OK_TEST[:-1], "/evidence/q"], tmp_path)


def test_code_state_from_env_or_clear_error(monkeypatch):
    assert code_state({"GIT_SHA": "abc", "GIT_DIRTY": "0"}, use_git=False) == ("abc", False)
    assert code_state({"GIT_SHA": "abc", "GIT_DIRTY": "1"}, use_git=False) == ("abc", True)
    with pytest.raises(StateError, match="GIT_SHA"):
        code_state({}, use_git=False)
    monkeypatch.delenv("FROZEN_SHA", raising=False)
    monkeypatch.setenv("FROZEN_SHA", "fff")
    assert frozen_sha() == "fff"


async def test_amain_test_without_git_state_gives_clean_error(tmp_path, monkeypatch, capsys):
    import evals.golden.run as gr

    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setattr(gr, "_git", lambda *a: (_ for _ in ()).throw(FileNotFoundError("git")))
    argv = [str(tmp_path) if x == "OUT" else x for x in OK_TEST]
    assert await gr.amain(parse(argv)) == 2
    assert "GIT_SHA" in capsys.readouterr().err


def test_round_states_counts_killed_rounds_as_aborted():
    rows = [{"split": "test", "round": 1, "sha": "a", "status": "started"},
            {"split": "test", "round": 2, "sha": "a", "status": "started"},
            {"split": "test", "round": 2, "sha": "a", "status": "done", "aborted": False},
            {"split": "dev", "round": 1, "sha": "a", "status": "started"}]  # fmt: skip
    st = round_states(rows, "test")
    assert [(r["round"], r["aborted"]) for r in st] == [(1, True), (2, False)]


def test_is_primary_only_first_run_on_frozen_sha():
    assert is_primary("abc", "abc", prior=[])
    assert not is_primary("abc", "abc", prior=[{"round": 1}])
    assert not is_primary("abc", "def", prior=[])
    assert not is_primary(None, "abc", prior=[])


def test_p_meta_reports_monte_carlo_se():
    from evals.golden.run import p_meta_table

    t = p_meta_table(0.1, 0.9, n_students=6, n_templates=6, n_sim=20)
    assert t["se"].keys() == t["p_meta"].keys()
    assert all(0 <= v <= 0.5 / 20**0.5 + 1e-9 for v in t["se"].values())


# ----------------------------------------------------------------------------- re-sumarização
def _fixture_dir(tmp_path, name, split, n=20):
    outs, items = [], {}
    for i in range(n):
        sid = f"s{i % 4}"
        it = DatasetItem(f"t{i}@{sid}", f"t{i}", "academico" if i % 2 else "financeiro",
                         "shared" if i % 3 else "test_only", (Check("q", required=("x",)),))  # fmt: skip  # noqa: E501
        items[it.id] = it
        outs.append(Outcome(it.id, sid, it.template_id, it.category, it.split,
                            "pass" if i % 5 else "fail", 1, cause=None if i % 5 else "redacao",
                            turns=(TurnRecord(f"resp {i}", pre_guard_answer=None),)))  # fmt: skip
    d = tmp_path / name
    d.mkdir()
    rr = RoundResult(outs, redone=2)
    strata = {f"s{i}": "g" for i in range(4)}
    meta = {"split": split, "round": 1, "sha": "abc", "model": "m"}
    s = summarize(rr, strata=strata, n_boot=30, meta=meta)
    (d / "quality.json").write_text(json.dumps(s))
    write_private(d, rr, items, strata)
    return d, outs


def test_resummarize_offline_with_labels_utility_judge_and_dev(tmp_path):
    dev, _ = _fixture_dir(tmp_path, "dev", "dev")
    d, outs = _fixture_dir(tmp_path, "test", "test")
    labels = tmp_path / "labels.csv"
    labels.write_text("item_id,pergunta,resposta,author_correct\n" + "".join(
        f"{o.item_id},q,a,{'s' if (o.status == 'pass') or k % 2 else 'n'}\n"
        for k, o in enumerate(outs)))  # fmt: skip
    util = tmp_path / "u.csv"
    util.write_text("item_id,util\na,s\nb,n\nc,s\n")
    judge = tmp_path / "judge.jsonl"
    judge.write_text("".join(json.dumps({"item_id": o.item_id, "verdict": k % 3 != 0}) + "\n"
                             for k, o in enumerate(outs)))  # fmt: skip
    s = resummarize(d, labels=labels, utility=util, judge=judge, dev_dir=dev, n_boot=30)
    assert s["sensitivity"]["n_labels"] == 20 and "pass_corrigido" in s["sensitivity"]
    assert s["kappa_juiz"]["n"] == 20 and s["utility"]["n"] == 3
    assert s["redone"] == 2 and s["items"] == 20 and s["dev_vs_test"]
    md = (d / "quality.md").read_text()
    assert "VPP" in md and "kappa" in md and "Dev x teste" in md
    assert json.loads((d / "quality.json").read_text())["sensitivity"] is not None
