"""Confirmação (lock no SQL), feedback e saídas tipadas do 👎, contra o Postgres da raia."""

import asyncio
import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

import app.api.feedback as feedback_mod
from app.agent.fakes import ScriptedLLM
from app.agent.llm import LLMResponse, ToolCall, Usage
from app.api import ROUTERS
from app.api.auth import COOKIE, issue_token
from app.config import AppSettings
from app.data.db import Pool, create_pool, load_scope, with_scope
from app.infra.stores.drafts import DraftStore
from app.infra.stores.history import HistoryStore
from app.infra.stores.labels import LabelCache
from app.infra.stores.turn_outputs import TurnOutputCache
from app.obs.metrics import METRICS
from app.service.turn import Admission, DefaultTurnService
from app.tools import protocols as P
from app.tools.base import DraftCard, ToolContext, validate_args
from tests.conftest import GOOD
from tests.integration.conftest import Db, Loaded, etl_settings
from tests.unit.redis_fake import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="function")
TODAY = date(2026, 10, 31)
FIXED_NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
ORIGIN = {"Origin": "http://test"}
ROOT = Path(__file__).resolve().parents[2]
TOOL = {t.spec.name: t for t in P.TOOLS}


@dataclass
class Env:
    loaded: Loaded
    pool: Pool
    redis: FakeRedis
    settings: AppSettings
    app: FastAPI

    @property
    def db(self) -> Db:
        return self.loaded.db

    def person(self, case: int) -> UUID:
        rows = json.loads((self.loaded.out / "golden_login.json").read_text())["cases"]
        return UUID(next(r["person_key"] for r in rows if r["case"] == case))

    def client(self, case: int) -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers=ORIGIN
        )
        c.cookies.set(COOKIE, issue_token(self.person(case), "chat", self.settings))
        return c

    async def draft(self, case: int, service: str, conv: str, **args: Any) -> DraftCard:
        pk = self.person(case)
        async with with_scope(self.pool, pk, ()) as c:
            scope = await load_scope(c, pk, LabelCache(self.redis))
        ctx = ToolContext(
            scope, self.settings, "web", conv, False, 1, False, TODAY,
            lambda: with_scope(self.pool, pk, scope.brand_families),
            DraftStore(self.redis, self.settings.limits),
        )  # fmt: skip
        spec = TOOL["draft_protocol"].spec
        out = await TOOL["draft_protocol"].handler(
            ctx, validate_args(spec, {"service_code": service, **args})
        )
        assert out.draft is not None, out.data
        return out.draft

    async def confirm(self, case: int, draft_id: str, **body: Any) -> httpx.Response:
        async with self.client(case) as c:
            return await c.post("/protocols/confirm", json={"draft_id": draft_id, **body})

    def rows(
        self,
        sql: str = "SELECT id, service_code, subject_code, description, opened_on,"
        " prazo, enrollment_index FROM agent_rw.protocol_request ORDER BY id",
    ) -> list[Any]:
        with self.db.connect("postgres") as c:
            return c.execute(sql).fetchall()  # type: ignore[call-overload,no-any-return]


@pytest.fixture
async def env(loaded_db: Loaded) -> AsyncIterator[Env]:
    pw = (loaded_db.db.secrets / "agent_app_password").read_text().strip()
    s = AppSettings(
        **{**GOOD, "agent_app_password": pw},  # type: ignore[arg-type]
        db_host="127.0.0.1", db_port=int(loaded_db.db.port), db_name=loaded_db.db.name,
    )  # fmt: skip
    pool = create_pool(s)
    await pool.open()
    app = FastAPI()
    for r in ROUTERS:
        app.include_router(r)
    redis = FakeRedis()
    app.state.settings, app.state.pool, app.state.redis = s, pool, redis
    try:
        yield Env(loaded_db, pool, redis, s, app)
    finally:
        await pool.close()


def _count(outcome: str) -> float:
    return METRICS["agent_draft_total"].labels(outcome=outcome)._value.get()  # type: ignore[union-attr]


async def test_confirm_once_concurrent(env: Env) -> None:
    card = await env.draft(1, "DOC-001", "c1")
    a, b = await asyncio.gather(env.confirm(1, card.draft_id), env.confirm(1, card.draft_id))
    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert len(env.rows()) == 1


async def test_two_drafts_same_service_concurrent_one_created(env: Env) -> None:
    c1 = await env.draft(1, "DOC-001", "c1")
    c2 = await env.draft(1, "DOC-001", "c2")  # outra conversa: dois rascunhos do mesmo serviço
    rs = await asyncio.gather(env.confirm(1, c1.draft_id), env.confirm(1, c2.draft_id))
    assert sorted(r.status_code for r in rs) == [200, 409]
    rows = env.rows()
    assert len(rows) == 1 and rows[0][0].startswith("SA-")


def test_lock_key_computed_in_sql_and_stable_across_processes(env_sync: Db) -> None:
    from app.api import protocols

    assert (
        "hashtextextended" in protocols.LOCK_SQL and "pg_advisory_xact_lock" in protocols.LOCK_SQL
    )
    src = (ROOT / "app/api/protocols.py").read_text()
    assert not re.search(r"(?<![A-Za-z_])hash\(", src)
    code = (
        "import psycopg, sys\nfrom app.api.protocols import LOCK_KEY_SQL\n"
        "c = psycopg.connect(host='127.0.0.1', port=int(sys.argv[1]), dbname=sys.argv[2],"
        " user='agent_app', password=sys.argv[3])\n"
        "print(c.execute(LOCK_KEY_SQL, {'person_key': sys.argv[4], 'service': 'DOC-001',"
        " 'subject': None}).fetchone()[0])\n"
    )
    pw = (env_sync.secrets / "agent_app_password").read_text().strip()
    outs = []
    for seed in ("1", "424242"):
        r = subprocess.run(
            [sys.executable, "-c", code, env_sync.port, env_sync.name, pw, str(UUID(int=0))],
            capture_output=True, text=True, cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed},
        )  # fmt: skip
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout.strip())
    assert outs[0] == outs[1] and outs[0].lstrip("-").isdigit()


@pytest.fixture
def env_sync(db: Db) -> Db:
    return db


async def test_second_gen090_allowed(env: Env) -> None:
    a = await env.draft(1, "GEN-090", "c1")
    b = await env.draft(1, "GEN-090", "c2")
    rs = [await env.confirm(1, a.draft_id), await env.confirm(1, b.draft_id)]
    assert [r.status_code for r in rs] == [200, 200]
    assert len({r[0] for r in env.rows()}) == 2


async def test_duplicate_same_service_not_eligible(env: Env) -> None:
    a = await env.draft(1, "DOC-001", "c1")
    b = await env.draft(1, "DOC-001", "c2")
    assert (await env.confirm(1, a.draft_id)).status_code == 200
    r = await env.confirm(1, b.draft_id)
    assert r.status_code == 409 and "protocolo aberto" in r.json()["detail"]
    assert len(env.rows()) == 1


async def test_aca021_other_subject_allowed_same_subject_blocked(env: Env) -> None:
    a = await env.draft(3, "ACA-021", "c1", subject="ADS0504")
    b = await env.draft(3, "ACA-021", "c2", subject="ADS5501")
    c = await env.draft(3, "ACA-021", "c3", subject="ADS0504")
    codes = [(await env.confirm(3, d.draft_id)).status_code for d in (a, b, c)]
    assert codes == [200, 200, 409]
    assert sorted(r[2] for r in env.rows()) == ["ADS0504", "ADS5501"]


async def test_description_edited_saved(env: Env) -> None:
    a = await env.draft(1, "DOC-002", "c1", description="para o estágio")
    r = await env.confirm(1, a.draft_id, description="  para a bolsa  ")
    assert r.status_code == 200
    assert env.rows()[0][3] == "para a bolsa"
    b = await env.draft(1, "DOC-003", "c2", description="original")
    assert (await env.confirm(1, b.draft_id)).status_code == 200  # sem edição: vale o rascunho
    assert sorted(x[3] for x in env.rows()) == ["original", "para a bolsa"]


async def test_sa_dates_from_reference(env: Env) -> None:
    a = await env.draft(1, "DOC-001", "c1")
    sa = (await env.confirm(1, a.draft_id)).json()["sa"]
    row = env.rows()[0]
    assert re.fullmatch(r"SA-2026-\d{6}", sa) and row[0] == sa
    assert row[4] == date(2026, 10, 31) and row[5] == date(2026, 11, 2) and row[6] == "1"


async def test_confirm_message_with_deadline(env: Env) -> None:
    a = await env.draft(1, "DOC-001", "c1")
    r = (await env.confirm(1, a.draft_id)).json()
    assert r["prazo"] == "02/11/2026"
    assert r["mensagem"] == f"Solicitação {r['sa']} aberta. Prazo: 02/11/2026."


async def test_confirm_refused_when_tainted(env: Env) -> None:
    a = await env.draft(1, "DOC-001", "c1")
    await HistoryStore(env.redis, env.settings.limits).mark_tainted(env.person(1), "web", "c1")
    assert (await env.confirm(1, a.draft_id)).status_code == 409
    assert env.rows() == []


async def test_confirm_note_grounds_action_claim(env: Env) -> None:
    a = await env.draft(1, "DOC-001", "c1")
    sa = (await env.confirm(1, a.draft_id)).json()["sa"]
    pk = env.person(1)
    async with with_scope(env.pool, pk, ()) as c:
        scope = await load_scope(c, pk, LabelCache(env.redis))
    text = f"Abri a solicitação {sa}."
    llm = ScriptedLLM(
        [LLMResponse(text, (), Usage(input_tokens=10, output_tokens=5), env.settings.llm_model)] * 3
    )
    svc = DefaultTurnService(env.settings, env.pool, env.redis, llm)
    adm = Admission(True, 200, None, None, "ok", "normal", pk, "web")

    async def emit(ev: Any) -> None:
        pass

    r = await svc.handle(
        admission=adm, scope=scope, conversation_id="c1", text="o que você fez?", channel="web",
        emit=emit,
    )  # fmt: skip
    assert r.guard == "pass" and sa in r.answer


async def test_feedback_isolated(env: Env) -> None:
    await TurnOutputCache(env.redis).put(env.person(1), "t1", {"status": "ok"})
    async with env.client(1) as c:
        r = await c.post("/feedback", json={"trace_id": "t1", "useful": True})
        again = await c.post("/feedback", json={"trace_id": "t1", "useful": False})
        free_text = await c.post(
            "/feedback", json={"trace_id": "t1", "useful": True, "comment": "x"}
        )
    assert r.status_code == 200 and again.status_code == 200  # o 2º voto não grava outra linha
    assert free_text.status_code == 422  # sem texto livre
    for case, expect in ((1, 1), (2, 0)):
        async with with_scope(env.pool, env.person(case), ()) as conn:
            n = (await (await conn.execute("SELECT count(*) FROM agent_rw.feedback")).fetchone())[0]  # type: ignore[index]
        assert n == expect
    assert env.rows("SELECT count(*) FROM agent_rw.turn_output")[0][0] == 0  # 👍 não guarda saída


@pytest.mark.parametrize("useful", [True, False])
async def test_idor_conversation_feedback(
    env: Env, monkeypatch: pytest.MonkeyPatch, useful: bool
) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(feedback_mod, "annotate", lambda *a: calls.append(a))
    await TurnOutputCache(env.redis).put(env.person(1), "tr-a", {"status": "ok", "tool_calls": []})
    async with env.client(2) as c:  # outro aluno avalia um trace que não é dele
        r = await c.post("/feedback", json={"trace_id": "tr-a", "useful": useful})
    assert r.status_code == 404 and r.json() == {"mensagem": "Turno não encontrado."}
    assert env.rows("SELECT trace_id FROM agent_rw.turn_output") == []
    assert env.rows("SELECT count(*) FROM agent_rw.feedback")[0][0] == 0 and calls == []
    async with env.client(1) as c:
        assert (
            await c.post("/feedback", json={"trace_id": "tr-a", "useful": useful})
        ).status_code == 200
    assert env.rows("SELECT count(*) FROM agent_rw.feedback")[0][0] == 1
    assert [x[0] for x in calls] == ["tr-a"]
    assert len(env.rows("SELECT trace_id FROM agent_rw.turn_output")) == (0 if useful else 1)


def test_typed_only_allowlist_exact_keys() -> None:
    from app.api.feedback import ALLOWED_KEYS, typed_only

    full = {k: ("x" if k not in ("tool_calls", "records") else []) for k in ALLOWED_KEYS}
    noisy = {**full, "answer": "A", "pre_guard_answer": "B", "timings_ms": {}, "novo": 1}
    assert set(typed_only(noisy)) == set(ALLOWED_KEYS) == {
        "status", "guard", "answer_kind", "domain", "tools_class", "model", "tool_calls", "records"
    }  # fmt: skip
    rec = {"kind": "k", "id": "1", "aliases": ["a"], "fields": {"prazo": "02/11/2026",
           "valor": "R$ 20,00", "obs": "texto livre do aluno com várias palavras"}}  # fmt: skip
    out = typed_only({**full, "records": [rec]})["records"][0]
    assert out == {"kind": "k", "id": "1", "fields": {"prazo": "02/11/2026",
                   "valor": "R$ 20,00", "obs": "[omitido]"}}  # fmt: skip


async def test_confirm_survives_redis_failure_after_commit(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = await env.draft(1, "DOC-001", "c1")

    async def boom(*args: Any, **kw: Any) -> None:
        raise ConnectionError("redis fora")

    monkeypatch.setattr(HistoryStore, "append", boom)
    r = await env.confirm(1, a.draft_id)
    assert r.status_code == 200 and r.json()["sa"] == env.rows()[0][0]


async def test_empty_description_keeps_draft_text(env: Env) -> None:
    a = await env.draft(1, "DOC-002", "c1", description="texto do rascunho")
    assert (await env.confirm(1, a.draft_id, description="")).status_code == 200
    assert env.rows()[0][3] == "texto do rascunho"


async def test_thumbs_down_persists_typed_outputs_7d(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(feedback_mod, "wall_now", lambda: FIXED_NOW)
    payload = {
        "answer": "RESPOSTA DO AGENTE", "status": "ok", "guard": "pass", "answer_kind": "answer",
        "domain": "protocolos", "tools_class": "1", "model": "m",
        "tool_calls": [
            {"name": "draft_protocol", "returned_records": 1,
             "args": {"service_code": "DOC-001", "description": "TEXTO DO ALUNO",
                      "enrollment": "1"}},
            {"name": "get_protocol", "returned_records": 1, "args": {"numero": "20260024855"}},
        ],
        "records": [{"kind": "protocolo", "id": "20260024855", "aliases": [],
                     "fields": {"status": "aberto"}}],
    }  # fmt: skip
    await TurnOutputCache(env.redis).put(env.person(1), "tr-1", payload)
    async with env.client(1) as c:
        assert (
            await c.post("/feedback", json={"trace_id": "tr-1", "useful": False})
        ).status_code == 200
        assert (
            await c.post("/feedback", json={"trace_id": "tr-1", "useful": False})
        ).status_code == 200
    rows = env.rows(
        "SELECT trace_id, person_key, created_at, expires_at, payload FROM agent_rw.turn_output"
    )
    assert len(rows) == 1
    assert rows[0][1] == env.person(1) and rows[0][3] == FIXED_NOW + timedelta(days=7)
    blob = json.dumps(rows[0][4], ensure_ascii=False)
    assert (
        "RESPOSTA DO AGENTE" not in blob
        and "TEXTO DO ALUNO" not in blob
        and "answer" not in rows[0][4]
    )
    assert rows[0][4]["tool_calls"][0]["args"]["service_code"] == "DOC-001"  # enumerado fica
    assert rows[0][4]["tool_calls"][1]["args"]["numero"] == "20260024855"
    assert rows[0][4]["records"][0]["id"] == "20260024855" and rows[0][4]["answer_kind"] == "answer"


def _seed_turn_outputs(env: Env) -> None:
    with env.db.connect("postgres") as c:
        for tid, exp in (
            ("old", FIXED_NOW - timedelta(seconds=1)),
            ("new", FIXED_NOW + timedelta(days=1)),
        ):
            c.execute(
                "INSERT INTO agent_rw.turn_output VALUES (%s, %s, %s, %s, %s::jsonb)",
                (tid, env.person(1), FIXED_NOW - timedelta(days=8), exp, json.dumps({"k": tid})),
            )


async def test_expired_turn_output_purged_and_invisible(env: Env) -> None:
    from etl.purge import purge
    from scripts.investigate import fetch

    _seed_turn_outputs(env)
    with env.db.connect("ops_audit") as c:
        assert fetch(c, "old", FIXED_NOW) is None  # expirado: invisível
        assert fetch(c, "new", FIXED_NOW) == {"k": "new"}
        with pytest.raises(Exception, match="timestamp"):  # sem SET app.now, falha alto
            c.execute("SELECT * FROM agent_rw.turn_output")
    assert purge(etl_settings(env.db), database=env.db.name, now=FIXED_NOW) == 1
    assert [r[0] for r in env.rows("SELECT trace_id FROM agent_rw.turn_output")] == ["new"]


async def test_draft_metric_outcomes(env: Env) -> None:
    pk = env.person(1)
    async with with_scope(env.pool, pk, ()) as c:
        scope = await load_scope(c, pk, LabelCache(env.redis))
    call = ToolCall("c1", "draft_protocol", {"service_code": "DOC-001"}, None)
    use = Usage(input_tokens=10, output_tokens=5)
    m = env.settings.llm_model
    llm = ScriptedLLM(
        [LLMResponse(None, (call,), use, m), LLMResponse("Preparei o rascunho.", (), use, m)] * 2
    )
    svc = DefaultTurnService(env.settings, env.pool, env.redis, llm)
    adm = Admission(True, 200, None, None, "ok", "normal", pk, "web")
    events: list[Any] = []

    async def emit(ev: Any) -> None:
        events.append(ev)

    exibido, confirmado, expirado = _count("exibido"), _count("confirmado"), _count("expirado")
    await svc.handle(admission=adm, scope=scope, conversation_id="c1", text="quero uma declaração",
                     channel="web", emit=emit)  # fmt: skip
    draft = next(e for e in events if e["event"] == "draft")
    assert _count("exibido") == exibido + 1
    assert (await env.confirm(1, draft["draft_id"])).status_code == 200
    assert _count("confirmado") == confirmado + 1
    card = await env.draft(1, "DOC-002", "c9")  # vence sem confirmação
    await env.redis.delete(DraftStore._k(pk, "d", card.draft_id))
    assert await DraftStore(env.redis, env.settings.limits).pending(pk, "c9") is None
    assert _count("expirado") == expirado + 1
