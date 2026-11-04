import asyncio
from uuid import uuid4

from app.agent.llm import Message, ToolCall
from app.config import AppSettings
from app.infra.stores.drafts import DraftStore
from app.infra.stores.history import HistoryStore
from app.infra.stores.labels import LabelCache
from app.infra.stores.tickets import LoginTickets
from app.obs.metrics import METRICS
from app.tools.base import DraftCard, DraftKey
from core.rkey import rkey
from tests.conftest import GOOD
from tests.unit.redis_fake import FakeRedis

SUB, CONV = uuid4(), "c1"


def _limits(app_secrets):  # type: ignore[no-untyped-def]
    return AppSettings().limits


def _card(did: str = "d1", subject: str | None = None, conv: str = CONV) -> DraftCard:
    return DraftCard(did, DraftKey("ACA-020", subject), "2ª chamada", "Curso 1", "R$ 0,00",
                     "05/11/2026", "desc", "aviso", conv, "1")  # fmt: skip


def _turn(i: int) -> tuple[Message, ...]:
    tc = ToolCall("x", "get_catalog", {"a": 1}, b"\x00\xff")
    return (
        Message("user", f"oi {i}", turn=i),
        Message("assistant", None, (tc,), provider_meta=b"\x01", turn=i),
        Message("tool", None, tool_call_id="x", tool_name="get_catalog",
                tool_result={"total": i}, untrusted=True, turn=i),
    )  # fmt: skip


async def test_history_roundtrip_and_cut(app_secrets) -> None:  # type: ignore[no-untyped-def]
    lim = _limits(app_secrets)
    h = HistoryStore(FakeRedis(), lim)
    assert (await h.load(SUB, "web", CONV)).turns == ()
    for i in range(8):
        await h.append(SUB, "web", CONV, _turn(i), draft_only=False)
    c = await h.load(SUB, "web", CONV)
    assert len(c.turns) == lim.history_turns == 5
    assert c.turns[-1] == _turn(7) and c.turns[0] == _turn(3)  # bytes e dict sobrevivem
    assert (await h.load(SUB, "web", "outra")).turns == ()


async def test_taint_sticky_for_conversation(app_secrets) -> None:  # type: ignore[no-untyped-def]
    h = HistoryStore(FakeRedis(), _limits(app_secrets))
    await h.append(SUB, "web", CONV, _turn(0), draft_only=False)
    await h.mark_tainted(SUB, "web", CONV)
    for i in range(1, 11):
        await h.append(SUB, "web", CONV, _turn(i), draft_only=False)
    c = await h.load(SUB, "web", CONV)
    assert c.tainted is True and len(c.turns) == 5
    assert (await h.load(SUB, "web", "outra")).tainted is False


async def test_other_enrollment_offered_flag(app_secrets) -> None:  # type: ignore[no-untyped-def]
    h = HistoryStore(FakeRedis(), _limits(app_secrets))
    assert (await h.load(SUB, "web", CONV)).other_enrollment_offered is False
    await h.mark_other_enrollment_offered(SUB, "web", CONV)
    await h.append(SUB, "web", CONV, _turn(0), draft_only=False)
    c = await h.load(SUB, "web", CONV)
    assert c.other_enrollment_offered is True and c.tainted is False
    assert (await h.load(SUB, "web", "outra")).other_enrollment_offered is False


async def test_last_turn_draft_only_flag(app_secrets) -> None:  # type: ignore[no-untyped-def]
    h = HistoryStore(FakeRedis(), _limits(app_secrets))
    await h.append(SUB, "web", CONV, _turn(0), draft_only=True)
    assert (await h.load(SUB, "web", CONV)).last_turn_draft_only is True
    await h.append(SUB, "web", CONV, _turn(1), draft_only=False)
    assert (await h.load(SUB, "web", CONV)).last_turn_draft_only is False


async def test_history_ttl(app_secrets) -> None:  # type: ignore[no-untyped-def]
    r = FakeRedis()
    h = HistoryStore(r, _limits(app_secrets))
    await h.append(SUB, "web", CONV, _turn(0), draft_only=False)
    ttl = await r._r.ttl(rkey("conv", SUB, "web", f"t:{CONV}"))
    assert 0 < ttl <= 1800


async def test_draft_single_use_owner_bound(app_secrets) -> None:  # type: ignore[no-untyped-def]
    d = DraftStore(FakeRedis(), _limits(app_secrets))
    await d.put(SUB, CONV, _card())
    assert await d.take(uuid4(), "d1") is None  # outro sub
    got = await d.take(SUB, "d1")
    assert got == _card()
    assert await d.take(SUB, "d1") is None
    assert await d.pending(SUB, CONV) is None


async def test_draft_same_pending_by_service_and_subject(app_secrets) -> None:  # type: ignore[no-untyped-def]
    d = DraftStore(FakeRedis(), _limits(app_secrets))
    await d.put(SUB, CONV, _card(subject="ADM0103"))
    assert (await d.pending(SUB, CONV)) == _card(subject="ADM0103")
    assert await d.same_pending(SUB, CONV, DraftKey("ACA-020", "ADM0103")) == _card(
        subject="ADM0103"
    )
    assert await d.same_pending(SUB, CONV, DraftKey("ACA-020", "OUTRA")) is None
    assert await d.same_pending(SUB, "outra", DraftKey("ACA-020", "ADM0103")) is None


async def test_draft_ttl_and_expiry_marker(app_secrets) -> None:  # type: ignore[no-untyped-def]
    r = FakeRedis()
    d = DraftStore(r, _limits(app_secrets))
    await d.put(SUB, CONV, _card())
    assert 0 < await r._r.ttl(rkey("draft", SUB, "web", "d:d1")) <= 600
    marker = rkey("draft", SUB, "web", f"x:{CONV}")
    assert 600 < await r._r.ttl(marker) <= 1800
    counter = METRICS["agent_draft_total"].labels(outcome="expirado")  # type: ignore[union-attr]
    before = counter._value.get()
    assert await d.pending(SUB, CONV) is not None and counter._value.get() == before
    await r._r.delete(rkey("draft", SUB, "web", "d:d1"))  # o TTL de 600 s venceu
    assert await d.pending(SUB, CONV) is None
    assert counter._value.get() == before + 1 and await r._r.get(marker) is None
    assert await d.pending(SUB, CONV) is None and counter._value.get() == before + 1


async def test_draft_take_clears_marker_without_expiry(app_secrets) -> None:  # type: ignore[no-untyped-def]
    r = FakeRedis()
    d = DraftStore(r, _limits(app_secrets))
    await d.put(SUB, CONV, _card())
    await d.take(SUB, "d1")
    counter = METRICS["agent_draft_total"].labels(outcome="expirado")  # type: ignore[union-attr]
    before = counter._value.get()
    assert await d.pending(SUB, CONV) is None and counter._value.get() == before


async def test_ticket_single_use_ttl(app_secrets) -> None:  # type: ignore[no-untyped-def]
    r = FakeRedis()
    t = LoginTickets(r, _limits(app_secrets))
    pk = uuid4()
    tid = await t.issue(pk)
    assert 0 < await r._r.ttl(rkey("ticket", "global", "-", tid)) <= 300
    assert await t.consume("inexistente") is None
    assert await t.consume(tid) == pk
    assert await t.consume(tid) is None
    a, b = await asyncio.gather(t.issue(pk), t.issue(pk))
    assert a != b


async def test_ticket_concurrent_consume_once(app_secrets) -> None:  # type: ignore[no-untyped-def]
    t = LoginTickets(FakeRedis(), _limits(app_secrets))
    pk = uuid4()
    tid = await t.issue(pk)
    got = await asyncio.gather(*(t.consume(tid) for _ in range(5)))
    assert got.count(pk) == 1 and got.count(None) == 4


async def test_label_cache() -> None:
    r = FakeRedis()
    c = LabelCache(r)
    assert await c.get("course:1") is None
    await c.put("course:1", "Direito")
    assert await c.get("course:1") == "Direito"
    assert await r._r.get(rkey("labels", "global", "-", "course:1")) == b"Direito"


class _Yielding(FakeRedis):
    """Cede o loop antes de cada operação: força o interleaving que o fakeredis não produz."""

    async def get(self, key):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return await super().get(key)

    async def set(self, key, value, **kw):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0)
        return await super().set(key, value, **kw)


async def test_concurrent_turns_keep_taint_and_flags(app_secrets) -> None:  # type: ignore[no-untyped-def]
    h = HistoryStore(_Yielding(), _limits(app_secrets))
    for _ in range(20):  # interleavings variados: nenhum pode perder flag nem turno
        conv = uuid4().hex
        await asyncio.gather(
            h.append(SUB, "web", conv, _turn(1), draft_only=False),
            h.mark_tainted(SUB, "web", conv),
            h.mark_other_enrollment_offered(SUB, "web", conv),
            h.append(SUB, "web", conv, _turn(2), draft_only=False),
        )
        c = await h.load(SUB, "web", conv)
        assert c.tainted and c.other_enrollment_offered and len(c.turns) == 2


async def test_history_conversation_ids_do_not_collide(app_secrets) -> None:  # type: ignore[no-untyped-def]
    h = HistoryStore(FakeRedis(), _limits(app_secrets))
    await h.mark_tainted(SUB, "web", "a")
    assert (await h.load(SUB, "web", "t:a")).tainted is False
    await h.append(SUB, "web", "t:a", _turn(0), draft_only=True)
    assert (await h.load(SUB, "web", "a")).turns == ()


async def test_draft_id_cannot_reach_pointer_or_marker(app_secrets) -> None:  # type: ignore[no-untyped-def]
    r = FakeRedis()
    d = DraftStore(r, _limits(app_secrets))
    await d.put(SUB, CONV, _card())
    for bad in (f"conv:{CONV}", f"exp:{CONV}", f"p:{CONV}", f"x:{CONV}", "", "a" * 200):
        assert await d.take(SUB, bad) is None
    assert await d.pending(SUB, CONV) == _card()  # ponteiro e marcador intactos
    await r._r.set(rkey("draft", SUB, "web", "d:lixo"), b"nao-json")
    assert await d.take(SUB, "lixo") is None


async def test_take_pending_tolerate_corrupt_blob(app_secrets) -> None:  # type: ignore[no-untyped-def]
    r = FakeRedis()
    d = DraftStore(r, _limits(app_secrets))
    await d.put(SUB, CONV, _card())
    await r._r.set(rkey("draft", SUB, "web", "d:d1"), b"{")
    assert await d.pending(SUB, CONV) is None


def test_conninfo_quotes_password(app_secrets) -> None:  # type: ignore[no-untyped-def]
    from psycopg.conninfo import conninfo_to_dict

    from app.data.db import conninfo

    s = AppSettings(**{**GOOD, "agent_app_password": "a b'c\\d" + "z" * 30}, db_host="h")  # type: ignore[arg-type]
    got = conninfo_to_dict(conninfo(s))
    assert (
        got["password"] == "a b'c\\d" + "z" * 30
        and got["user"] == "agent_app"
        and got["host"] == "h"
    )
