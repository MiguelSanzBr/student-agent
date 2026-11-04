import json
from collections.abc import AsyncIterator
from datetime import date
from uuid import UUID

import pytest

from app.config import AppSettings
from app.data.db import create_pool, load_scope, with_scope
from app.data.queries import finance as q
from app.infra.stores.labels import LabelCache
from app.tools.base import ToolContext, ToolError
from app.tools.finance import TOOLS
from core.format import fmt_brl, fmt_date
from evals import oracle as o
from tests.conftest import GOOD
from tests.integration.conftest import MINI, Loaded
from tests.unit.redis_fake import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="function")
TODAY = date(2026, 10, 31)
SIBLING = ("UniSaoPaulo", "180548")
H = {t.spec.name: t.handler for t in TOOLS}


@pytest.fixture(scope="module")
def raw() -> o.Raw:
    return o.load_csv(MINI)


def _golden() -> dict[int, dict]:  # type: ignore[type-arg]
    g = json.loads((MINI.parent / "golden.json").read_text())["cases"]
    return {c["case"]: c for c in g}


def _enrs(case: int) -> list[o.Enr]:
    c = _golden()[case]
    return [(c["brand"], c["ra"]), *(tuple(e) for e in c["other_enrollments"])]  # type: ignore[misc]


@pytest.fixture
async def pool(loaded_db: Loaded) -> AsyncIterator:  # type: ignore[type-arg]
    pw = (loaded_db.db.secrets / "agent_app_password").read_text().strip()
    s = AppSettings(
        **{**GOOD, "agent_app_password": pw},  # type: ignore[arg-type]
        db_host="127.0.0.1", db_port=int(loaded_db.db.port), db_name=loaded_db.db.name,
    )  # fmt: skip
    p = create_pool(s)
    await p.open()
    try:
        yield p
    finally:
        await p.close()


async def _ctx(pool, loaded: Loaded, case: int) -> ToolContext:  # type: ignore[no-untyped-def]
    rows = json.loads((loaded.out / "golden_login.json").read_text())["cases"]
    pk = UUID(next(r["person_key"] for r in rows if r["case"] == case))
    async with with_scope(pool, pk, ()) as c:
        scope = await load_scope(c, pk, LabelCache(FakeRedis()))
    return ToolContext(
        scope, None, "web", "c1", False, 1, False, TODAY,  # type: ignore[arg-type]
        lambda: with_scope(pool, pk, scope.brand_families), None,  # type: ignore[arg-type]
    )  # fmt: skip


async def test_golden4_total_para_quitar(pool, loaded_db, raw) -> None:  # type: ignore[no-untyped-def]
    out = await H["get_financial_summary"](await _ctx(pool, loaded_db, 4), {})
    f = o.oracle_financial(raw, _enrs(4)[0])
    assert out.data["total_para_quitar"] == "R$ 49.508,43" == fmt_brl(f.total_para_quitar)
    assert out.data["principal_vencido"] == "R$ 42.636,26"
    assert out.data["encargos"] == "R$ 6.872,17"
    assert out.data["faturas_vencidas"] == 32 == f.faturas_vencidas
    assert out.data["proximo_vencimento"] == fmt_date(f.proximo_vencimento)  # type: ignore[arg-type]
    assert out.data["valor_proximo_vencimento"] == fmt_brl(f.proximo_valor)  # type: ignore[arg-type]
    assert out.data["dias_para_proximo_vencimento"] == (f.proximo_vencimento - TODAY).days  # type: ignore[operator]


async def test_records_financeiro_total(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["get_financial_summary"](await _ctx(pool, loaded_db, 4), {})
    r = next(r for r in out.records if r.key == "financeiro:1")
    assert r.fields["total_para_quitar"] == "R$ 49.508,43"
    assert set(r.fields) >= {"principal_vencido", "encargos", "faturas_vencidas",
                             "proximo_vencimento", "valor_proximo_vencimento"}  # fmt: skip
    assert r.aliases == ("matrícula 1",)


async def test_golden9_breakdown(pool, loaded_db, raw) -> None:  # type: ignore[no-untyped-def]
    out = await H["get_financial_summary"](await _ctx(pool, loaded_db, 9), {})
    want = sorted(o.oracle_financial(raw, e).total_para_quitar for e in _enrs(9))
    got = [r for r in out.records if r.kind == "financeiro"]
    assert len(got) == 2
    assert sorted(r.fields["total_para_quitar"] for r in got) == sorted(fmt_brl(w) for w in want)
    assert out.data["total_geral"] == fmt_brl(sum(want))
    assert len(out.data["matriculas"]) == 2


async def test_no_sibling_invoices(pool, loaded_db, raw) -> None:  # type: ignore[no-untyped-def]
    ce = o.contract_enrollment(raw)
    sib = {i["invoiceId"] for i in raw["finance/invoice"] if ce[i["contractId"]] == SIBLING}
    assert sib
    for case in _golden():
        ctx = await _ctx(pool, loaded_db, case)
        for e in ctx.scope.enrollments:
            async with ctx.db() as conn:
                inv, _, _ = await q.fetch_ledger(conn, e.enrollment_key)
            assert not sib & {i.id for i in inv}


async def test_golden1_no_agreements(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["list_agreements"](await _ctx(pool, loaded_db, 1), {})
    assert out.data["mensagem"] == "Nenhum registro encontrado." and out.data["total"] == 0
    assert out.records == ()


async def test_golden6_active_agreement(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["list_agreements"](await _ctx(pool, loaded_db, 6), {})
    a = next(a for a in out.data["acordos"] if a["acordo"] == "AG2026000340")
    assert a["status"] == "ativo" and a["parcelas"] == 2
    r = next(r for r in out.records if r.key == "acordo:AG2026000340")
    assert r.fields["status"] == "ativo"


async def test_cap_20(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["list_invoices"](await _ctx(pool, loaded_db, 4), {})
    assert len(out.data["faturas"]) == 20 and out.data["total"] > 20
    assert len(out.records) == 20


async def test_reference_date(pool, loaded_db, raw) -> None:  # type: ignore[no-untyped-def]
    f = o.oracle_financial(raw, _enrs(4)[0])
    out = await H["get_financial_summary"](await _ctx(pool, loaded_db, 4), {})
    assert out.data["dias_para_proximo_vencimento"] == (f.proximo_vencimento - TODAY).days  # type: ignore[operator]


async def test_period_is_competence(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["list_invoices"](await _ctx(pool, loaded_db, 4), {"period": "2026-10"})
    assert out.data["faturas"]
    assert all(i["competência"] == "2026-10" and i["vencimento"] for i in out.data["faturas"])
    with pytest.raises(ToolError):
        await H["list_invoices"](await _ctx(pool, loaded_db, 4), {"period": "10/2026"})


async def test_status_filter(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["list_invoices"](await _ctx(pool, loaded_db, 4), {"status": "vencida"})
    assert out.data["total"] == 32 and {i["status"] for i in out.data["faturas"]} == {"vencida"}


async def test_invoice_balance_and_to_settle_per_item(pool, loaded_db, raw) -> None:  # type: ignore[no-untyped-def]
    f = o.oracle_financial(raw, _enrs(4)[0])
    ctx = await _ctx(pool, loaded_db, 4)
    async with ctx.db() as conn:
        inv, pay, lc = await q.fetch_ledger(conn, ctx.scope.enrollments[0].enrollment_key)
    from app.rules.finance import invoice_balance, invoice_to_settle

    assert {i.id for i in inv if i.status in ("OPEN", "OVERDUE", "PARTIAL")} == set(f.saldo)
    for i in inv:
        if i.id in f.saldo:
            assert invoice_balance(i, pay) == f.saldo[i.id]
            assert invoice_to_settle(i, pay, lc) == f.para_quitar[i.id]
    out = await H["list_invoices"](ctx, {"status": "vencida"})
    for it in out.data["faturas"]:
        assert it["saldo"] == fmt_brl(f.saldo[it["fatura"]])
        assert it["para_quitar"] == fmt_brl(f.para_quitar[it["fatura"]])


async def test_finance_coverage_note(pool, loaded_db) -> None:  # type: ignore[no-untyped-def]
    out = await H["list_invoices"](await _ctx(pool, loaded_db, 4), {"period": "2020-06"})
    assert "Não há dados do financeiro antes de" in out.data["nota"]
    assert out.data["total"] == 0


async def test_in_collection_label() -> None:
    assert q.status_label("IN_COLLECTION", date(2026, 1, 1), TODAY) == "Em cobrança"
