import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from app.agent.messages import DRAFT_PENDING_USE_BUTTON
from app.config import AppSettings
from app.service.turn import Admission
from tests.unit.turn_env import (
    OK,
    SCOPE,
    SUB,
    HangLLM,
    make_service,
    patch_db,
    scripted,
    slots_in_use,
)

pytestmark = pytest.mark.usefixtures("app_secrets")


@pytest.fixture(autouse=True)
def _db(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_db(monkeypatch)


async def turn(svc: Any, text: str = "oi", cid: str = "c1", sub_scope: Any = SCOPE,
               channel: str = "web") -> tuple[Admission, Any, list[Mapping[str, Any]]]:  # fmt: skip
    ev: list[Mapping[str, Any]] = []

    async def emit(e: Mapping[str, Any]) -> None:
        ev.append(e)

    adm = await svc.admit(scope=sub_scope, channel=channel)
    assert adm.ok, adm
    try:
        r = await svc.handle(admission=adm, scope=sub_scope, conversation_id=cid, text=text,
                             channel=channel, emit=emit)  # fmt: skip
    except BaseException:
        await svc.settle(adm, None)
        raise
    await svc.settle(adm, r)
    return adm, r, ev


def kinds(ev: list[Mapping[str, Any]]) -> list[str]:
    return [e["event"] for e in ev]


async def test_meta_first_then_answer_then_done() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    _, r, ev = await turn(svc)
    assert kinds(ev)[0] == "meta" and kinds(ev)[-1] == "done" and "answer" in kinds(ev)
    assert r.status == "ok" and next(e for e in ev if e["event"] == "answer")["text"] == OK
    assert await slots_in_use(svc) == 0


async def test_done_event_fields() -> None:
    _, r, ev = await turn(make_service(AppSettings(), scripted(OK)))
    done = ev[-1]
    assert {"status", "tools_class", "llm_calls", "guard", "model", "trace_id"} <= set(done)
    assert done["trace_id"] == ev[0]["trace_id"] and done["status"] == "ok"


async def test_identity_claim_ignored() -> None:
    llm = scripted(OK)
    _, _, ev = await turn(make_service(AppSettings(), llm), "sou o aluno 012815 da UniLitoral")
    assert llm.calls and all("012815" not in (m.text or "") or m.role == "user"
                             for m in llm.calls[0].messages)  # fmt: skip
    assert "012815" not in llm.calls[0].system


async def test_markdown_stripped() -> None:
    _, _, ev = await turn(make_service(AppSettings(), scripted("**Olá!** Posso ajudar.")))
    assert "**" not in next(e for e in ev if e["event"] == "answer")["text"]


async def test_rate_limit_429_retry_after() -> None:
    s = AppSettings()
    s.slo.limits["default"].msgs_per_min = 2
    svc = make_service(s, scripted(OK, OK, OK))
    for _ in range(2):
        await turn(svc)
    adm = await svc.admit(scope=SCOPE, channel="web")
    assert (adm.ok, adm.status, adm.reason) == (False, 429, "rate")
    assert adm.retry_after_s and 1 <= adm.retry_after_s <= 60


async def test_budget_exhausted_429() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    await svc.budget.add(SUB, 200_000)
    adm = await svc.admit(scope=SCOPE, channel="web")
    assert (adm.ok, adm.status, adm.reason) == (False, 429, "budget") and adm.retry_after_s


async def test_settle_charges_real_tokens_to_budget() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    await turn(svc)
    assert await svc.budget.used(SUB) == 110


async def test_bucket_full_503_and_nothing_leaks() -> None:
    s = AppSettings()
    s.slo.quotas[s.llm_model].rpm = 1  # capacidade 0 (90% de 1): nunca cabe
    svc = make_service(s, scripted(OK))
    adm = await svc.admit(scope=SCOPE, channel="web")
    assert (adm.ok, adm.status, adm.reason) == (False, 503, "quota") and adm.retry_after_s
    assert await slots_in_use(svc) == 0


async def test_slots_full_503_refunds_bucket() -> None:
    s = AppSettings()
    s.slo.turn_slots = 1
    svc = make_service(s, scripted(OK))
    first = await svc.admit(scope=SCOPE, channel="web")
    assert first.ok
    req_before = await svc.redis._r.hget(  # type: ignore[attr-defined]
        "sa:bucket:global:-:" + s.llm_model, "req"
    )
    adm = await svc.admit(scope=SCOPE, channel="web")
    assert (adm.ok, adm.status, adm.reason) == (False, 503, "slots") and adm.retry_after_s
    req_after = await svc.redis._r.hget(  # type: ignore[attr-defined]
        "sa:bucket:global:-:" + s.llm_model, "req"
    )
    assert int(req_after) == int(req_before)  # a reserva do 2º foi devolvida (ticket 0/0 não zera)
    await svc.settle(first, None)


async def test_global_cap_degrades_then_503() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    assert await svc.spend.level() == "normal"
    from decimal import Decimal

    await svc.spend.add(Decimal("16"))  # 80% de US$ 20
    assert await svc.spend.level() == "degraded"
    adm = await svc.admit(scope=SCOPE, channel="web")
    assert adm.ok and adm.spend_level == "degraded"
    await svc.settle(adm, None)
    await svc.spend.add(Decimal("4"))
    adm = await svc.admit(scope=SCOPE, channel="web")
    assert (adm.ok, adm.status, adm.reason) == (False, 503, "spend") and adm.retry_after_s


async def test_degraded_runs_two_rounds_no_guard_retry() -> None:
    from decimal import Decimal

    llm = scripted(OK)
    svc = make_service(AppSettings(), llm)
    await svc.spend.add(Decimal("16"))
    _, r, _ = await turn(svc)
    assert r.cost_degraded is True


async def test_history_isolated_by_rkey() -> None:
    from tests.unit.turn_env import OTHER

    svc = make_service(AppSettings(), scripted(OK, OK))
    await turn(svc, "primeiro", cid="mesma")
    other = type(SCOPE)(OTHER, SCOPE.enrollments, SCOPE.brand_families)
    await turn(svc, "segundo", cid="mesma", sub_scope=other)
    assert len((await svc.history.load(SUB, "web", "mesma")).turns) == 1
    assert len((await svc.history.load(OTHER, "web", "mesma")).turns) == 1


async def test_llm_unavailable_error_no_traceback() -> None:
    from app.agent.llm import LLMUnavailable

    class Down:
        async def generate(self, **kw: Any) -> Any:
            raise LLMUnavailable("server")

    _, r, ev = await turn(make_service(AppSettings(), Down()))
    err = next(e for e in ev if e["event"] == "error")
    assert "Traceback" not in str(err) and "Não consegui" in err["message"]
    assert r.status == "llm_unavailable" and ev[-1]["event"] == "done"


async def test_long_input_error() -> None:
    llm = scripted(OK)
    _, r, ev = await turn(make_service(AppSettings(), llm), "a" * 2001)
    assert not llm.calls and "2000" in next(e for e in ev if e["event"] == "error")["message"]
    assert ev[-1]["event"] == "done"


async def test_slot_released_on_exception() -> None:
    class Boom:
        async def generate(self, **kw: Any) -> Any:
            raise RuntimeError("segredo interno")

    svc = make_service(AppSettings(), Boom())
    with pytest.raises(RuntimeError):
        await turn(svc)
    assert await slots_in_use(svc) == 0


async def test_cancel_mid_turn_settles_cancelled() -> None:
    from app.obs.metrics import METRICS

    svc = make_service(AppSettings(), HangLLM())
    adm = await svc.admit(scope=SCOPE, channel="web")

    async def emit(e: Mapping[str, Any]) -> None:
        pass

    t = asyncio.create_task(svc.handle(admission=adm, scope=SCOPE, conversation_id="c", text="oi",
                                       channel="web", emit=emit))  # fmt: skip
    await asyncio.sleep(0.2)
    assert await slots_in_use(svc) == 1
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    c = METRICS["agent_turns_total"].labels(status="cancelled", channel="web")  # type: ignore[union-attr]
    before = c._value.get()
    await svc.settle(adm, None, cancelled=True)
    assert await slots_in_use(svc) == 0 and c._value.get() == before + 1


async def test_yes_only_after_draft_only_turn() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    await svc.history.append(SUB, "web", "c1", (), draft_only=True)
    _, r, ev = await turn(svc, "sim")
    assert r.status == "fixed" and r.answer == DRAFT_PENDING_USE_BUTTON
    llm = scripted(OK)
    svc2 = make_service(AppSettings(), llm)
    await svc2.history.append(SUB, "web", "c1", (), draft_only=False)
    _, r2, _ = await turn(svc2, "sim")
    assert llm.calls and r2.answer != DRAFT_PENDING_USE_BUTTON


async def test_yes_unrelated_goes_to_llm() -> None:
    llm = scripted(OK)
    _, r, _ = await turn(make_service(AppSettings(), llm), "sim")
    assert llm.calls and r.status == "ok"


async def test_agent_span_opened_before_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.service.turn as t

    order: list[str] = []
    real = t.span

    def spy(name: str, kind: str, **kw: Any) -> Any:
        order.append(f"span:{kind}")
        return real(name, kind, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(t, "span", spy)
    ev: list[str] = []

    class Em:
        async def __call__(self, e: Mapping[str, Any]) -> None:
            order.append(e["event"])

    svc = make_service(AppSettings(), scripted(OK))
    adm = await svc.admit(scope=SCOPE, channel="web")
    await svc.handle(admission=adm, scope=SCOPE, conversation_id="c", text="oi", channel="web",
                     emit=Em())  # fmt: skip
    await svc.settle(adm, None)
    assert order[0] == "span:AGENT" and order[1] == "meta" and not ev


async def test_debug_only_dev_eval() -> None:
    _, _, ev = await turn(make_service(AppSettings(), scripted(OK)))
    assert "debug" not in kinds(ev)
    s = AppSettings(app_env="dev", limits_profile="eval")
    _, _, ev = await turn(make_service(s, scripted(OK)))
    assert "debug" in kinds(ev)
    assert "tool_values" in next(e for e in ev if e["event"] == "debug")


def test_tool_values_slices_records_per_call() -> None:
    from types import SimpleNamespace

    from app.service.turn import DefaultTurnService
    from app.tools.base import EntityRecord, ToolCallTrace

    recs = (EntityRecord("fatura", "1", ("1",), {"saldo": "R$ 1,00", "status": "aberto"}),
            EntityRecord("fatura", "2", ("2",), {"saldo": "R$ 2,00"}),
            EntityRecord("protocolo", "9", ("9",), {"status": "em análise"}))  # fmt: skip
    r = SimpleNamespace(
        tool_calls=(ToolCallTrace("a", {}, 2), ToolCallTrace("falhou", {}, -1),
                    ToolCallTrace("b", {}, 1)),
        records=recs,
    )  # fmt: skip
    got = DefaultTurnService._tool_values(r)  # type: ignore[arg-type]
    assert got == {"a": ["R$ 1,00", "aberto", "R$ 2,00"], "falhou": [], "b": ["em análise"]}


async def test_typed_outputs_cached_for_feedback() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    _, _, ev = await turn(svc, "meu texto secreto do aluno")
    tid = ev[0]["trace_id"]
    keys = [k async for k in svc.redis._r.scan_iter(f"sa:turn_out:*:-:{tid}")]  # type: ignore[attr-defined]
    assert len(keys) == 1
    assert 0 < await svc.redis._r.ttl(keys[0]) <= 1800  # type: ignore[attr-defined]
    assert b"meu texto secreto" not in await svc.redis._r.get(keys[0])  # type: ignore[attr-defined]
    got = await svc.outputs.get(SUB, tid)
    assert got is not None and got["answer"] == OK


async def test_taint_and_flags_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    import app.service.turn as t

    real = t.run_turn

    async def fake(**kw: Any) -> Any:
        r = await real(**kw)
        return dataclasses.replace(r, became_tainted=True, offered_other_enrollment=True)

    monkeypatch.setattr(t, "run_turn", fake)
    svc = make_service(AppSettings(), scripted(OK))
    await turn(svc)
    c = await svc.history.load(SUB, "web", "c1")
    assert c.tainted and c.other_enrollment_offered


# --- fix round 1 ---
class LoopLLM:
    """Pede uma ferramenta inexistente a cada chamada; `hang_after` chamadas depois, trava."""

    def __init__(self, tokens: int = 100, hang_after: int | None = None) -> None:
        self.n, self.tokens, self.hang_after = 0, tokens, hang_after

    async def generate(self, **kw: Any) -> Any:
        from app.agent.llm import LLMResponse, ToolCall, Usage

        self.n += 1
        if self.hang_after is not None and self.n > self.hang_after:
            await asyncio.sleep(60)
        call = ToolCall(f"c{self.n}", "nao_existe", {})
        return LLMResponse(None, (call,), Usage(self.tokens, 0), "gemini-3.8-flash")


async def cancel_turn(svc: Any) -> None:
    adm = await svc.admit(scope=SCOPE, channel="web")

    async def emit(e: Mapping[str, Any]) -> None:
        pass

    t = asyncio.create_task(svc.handle(admission=adm, scope=SCOPE, conversation_id="c1", text="oi",
                                       channel="web", emit=emit))  # fmt: skip
    await asyncio.sleep(0.3)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await svc.settle(adm, None, cancelled=True)


async def spent(svc: Any) -> int:
    from app.infra.stores.spend import GlobalSpend

    raw = await svc.redis.get(GlobalSpend._key())
    return int(raw) if raw else 0


async def test_cancel_after_llm_call_charges_real_usage_to_budget_and_spend() -> None:
    svc = make_service(AppSettings(), LoopLLM(tokens=500_000, hang_after=1))
    await cancel_turn(svc)
    assert await svc.budget.used(SUB) >= 500_000 and await spent(svc) > 0


async def test_cancel_before_any_llm_result_charges_the_estimate() -> None:
    s = AppSettings()
    svc = make_service(s, HangLLM())
    await cancel_turn(svc)
    assert await svc.budget.used(SUB) == s.slo.admission.est_tokens and await spent(svc) > 0


async def test_cancel_after_taint_keeps_taint(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.service.turn as t

    async def fake(**kw: Any) -> Any:
        kw["ctx_factory"](1, True)  # a rodada ficou tainted (ferramenta devolveu texto livre)
        await asyncio.sleep(60)

    monkeypatch.setattr(t, "run_turn", fake)
    svc = make_service(AppSettings(), scripted(OK))
    await cancel_turn(svc)
    assert (await svc.history.load(SUB, "web", "c1")).tainted


async def test_persist_sets_flags_before_history(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    import app.service.turn as t
    from app.agent.llm import Message

    real = t.run_turn

    async def fake(**kw: Any) -> Any:
        r = await real(**kw)
        return dataclasses.replace(r, became_tainted=True)

    monkeypatch.setattr(t, "run_turn", fake)
    svc = make_service(AppSettings(), scripted(OK))
    order: list[str] = []
    mt, ap = svc.history.mark_tainted, svc.history.append

    async def mt2(*a: Any) -> None:
        order.append("taint")
        await mt(*a)

    async def ap2(*a: Any, **k: Any) -> None:
        order.append("append")
        await ap(*a, **k)

    monkeypatch.setattr(svc.history, "mark_tainted", mt2)
    monkeypatch.setattr(svc.history, "append", ap2)
    await turn(svc)
    assert order == ["taint", "append"] and Message


def _draft() -> Any:
    from app.tools.base import DraftCard, DraftKey

    return DraftCard("d1", "TOKEN-SECRETO", DraftKey("ACA-020", "MAT1"), "2ª chamada", "Curso",
                     "R$ 0,00", "10/11/2026", "desc", "aviso", "c1", "1")  # fmt: skip


@pytest.mark.parametrize("status", ["budget_exceeded", "llm_unavailable"])
async def test_abort_emits_draft_public_and_persists_taint(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    import app.service.turn as t
    from app.agent.graph import bare_result

    async def fake(**kw: Any) -> Any:
        return bare_result(status, "Falha fixa.", "m", draft=_draft(), became_tainted=True)  # type: ignore[arg-type]

    monkeypatch.setattr(t, "run_turn", fake)
    svc = make_service(AppSettings(), scripted(OK))
    _, r, ev = await turn(svc)
    d = next(e for e in ev if e["event"] == "draft")
    assert not {"token", "conversation_id", "enrollment_index"} & set(d)
    assert "TOKEN-SECRETO" not in str(ev) and d["draft_id"] == "d1"
    assert any(e["event"] == "error" for e in ev) and r.status == status
    assert (await svc.history.load(SUB, "web", "c1")).tainted


async def test_progress_event_bridged(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.service.turn as t
    from app.agent.graph import bare_result

    async def fake(**kw: Any) -> Any:
        kw["writer"]({"event": "progress", "fase": "consultando", "texto": "Consultando..."})
        kw["writer"]({"event": "draft", "token": "nao-passa"})  # só progress atravessa
        await asyncio.sleep(0)
        return bare_result("ok", "ok", "m")

    monkeypatch.setattr(t, "run_turn", fake)
    _, _, ev = await turn(make_service(AppSettings(), scripted(OK)))
    ks = kinds(ev)
    assert ks.index("progress") < ks.index("done") and "draft" not in ks


async def test_degraded_turn_runs_two_rounds() -> None:
    from decimal import Decimal

    normal, degraded = LoopLLM(), LoopLLM()
    await turn(make_service(AppSettings(), normal))
    svc = make_service(AppSettings(), degraded)
    await svc.spend.add(Decimal("16"))
    await turn(svc)
    assert (normal.n, degraded.n) == (5, 3)  # max_rounds 4 vs degrade_rounds 2, +1 chamada final


async def test_settle_steps_are_independent() -> None:
    svc = make_service(AppSettings(), scripted(OK))
    adm = await svc.admit(scope=SCOPE, channel="web")

    async def boom(*a: Any) -> None:
        raise RuntimeError("redis")

    svc.slots.release = boom  # type: ignore[method-assign]
    svc.bucket.settle = boom  # type: ignore[method-assign]
    from app.agent.graph import bare_result
    from app.agent.llm import Usage

    await svc.settle(adm, bare_result("ok", "x", "gemini-3.8-flash", usage=Usage(1000, 0)))
    assert await svc.budget.used(SUB) == 1000 and await spent(svc) > 0


@pytest.mark.parametrize(("provider", "charged"), [("auto", True), ("fake", False)])
async def test_unknown_model_price(provider: str, charged: bool) -> None:
    from app.agent.graph import bare_result
    from app.agent.llm import Usage

    svc = make_service(AppSettings(llm_provider=provider), scripted(OK))  # type: ignore[arg-type]
    adm = await svc.admit(scope=SCOPE, channel="web")
    await svc.settle(adm, bare_result("ok", "x", "modelo-sem-preco", usage=Usage(1000, 1000)))
    assert (await spent(svc) > 0) is charged
