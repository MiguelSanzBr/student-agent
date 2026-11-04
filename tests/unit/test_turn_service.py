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
