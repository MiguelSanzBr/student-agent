import json
import re
from collections import Counter
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any
from uuid import UUID

import pytest

from app.config import AppSettings
from app.data.db import create_pool, load_scope, with_scope
from app.infra.stores.labels import LabelCache
from app.tools import catalog, lms
from app.tools.base import ToolContext, ToolOutput, orientation
from core.normalize import SP
from core.ontology import Ontology
from evals import oracle as o
from tests.conftest import GOOD
from tests.integration.conftest import MINI, Loaded
from tests.unit.redis_fake import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="function")
TODAY = date(2026, 10, 31)
GOLDEN = {1: ("UniSaoPaulo", "181722"), 2: ("UniAtlantico_EAD", "012815"),
          3: ("UniSaoPaulo_EAD", "014214"), 8: ("UniSaoPaulo", "182230"),
          9: ("UniSaoPaulo", "182915")}  # fmt: skip


@pytest.fixture(scope="module")
def raw() -> o.Raw:
    return o.load_csv(MINI)


def _onto() -> Ontology:
    from app.tools.base import default_ontology

    return default_ontology()


def _settings(loaded: Loaded) -> AppSettings:
    pw = (loaded.db.secrets / "agent_app_password").read_text().strip()
    return AppSettings(
        **{**GOOD, "agent_app_password": pw},  # type: ignore[arg-type]
        db_host="127.0.0.1", db_port=int(loaded.db.port), db_name=loaded.db.name,
    )  # fmt: skip


@pytest.fixture
async def make_ctx(loaded_db: Loaded) -> AsyncIterator[Any]:
    pool = create_pool(_settings(loaded_db))
    await pool.open()
    rows = json.loads((loaded_db.out / "golden_login.json").read_text())["cases"]

    async def make(case: int) -> ToolContext:
        pk = UUID(next(r["person_key"] for r in rows if r["case"] == case))
        async with with_scope(pool, pk, ()) as c:
            scope = await load_scope(c, pk, LabelCache(FakeRedis()))
        return ToolContext(
            scope, _settings(loaded_db), "web", "c1", False, 1, False, TODAY,
            lambda: with_scope(pool, pk, scope.brand_families), None,  # type: ignore[arg-type]
        )  # fmt: skip

    try:
        yield make
    finally:
        await pool.close()


async def _lms(ctx: ToolContext, **args: str) -> ToolOutput:
    return await lms.TOOLS[0].handler(ctx, args)


async def _cat(ctx: ToolContext, kind: str, **args: str) -> ToolOutput:
    return await catalog.TOOLS[0].handler(ctx, {"kind": kind, **args})


def _fact(out: ToolOutput, kind: str) -> list[Any]:
    return [r for r in out.records if r.kind == kind]


# ----------------------------------------------------------------------------- LMS
@pytest.mark.parametrize(("case", "n"), [(1, 7), (2, 30)])
async def test_golden_attempts(make_ctx: Any, raw: o.Raw, case: int, n: int) -> None:
    out = await _lms(await make_ctx(case))
    assert out.data["tentativas_nao_oficiais"] == n == o.oracle_lms(raw, GOLDEN[case]).tentativas
    assert "não valem nota" in out.data["aviso_tentativas"]


def _pending(raw: o.Raw, enr: o.Enr) -> int:
    """AV (vale nota) com janela aberta hoje e sem tentativa, direto do CSV."""
    ix = o._lms_index(raw)
    n = 0
    for uid in ix["by_enr"].get(enr, []):
        for course in ix["active"].get(uid, []):
            for q in ix["quizzes"].get(course, []):
                op, cl = o._brt_date(o._epoch(q["timeopen"])), o._brt_date(o._epoch(q["timeclose"]))
                n += (
                    q["name"] != "Practice Test"
                    and op <= TODAY <= cl
                    and not ix["attempts"][(uid, q["id"])]
                )
    return n


@pytest.mark.parametrize("case", [1, 2, 3, 8, 9])
async def test_pending_vs_oracle(make_ctx: Any, raw: o.Raw, case: int) -> None:
    out = await _lms(await make_ctx(case))
    assert out.data["pendentes"] == _pending(raw, GOLDEN[case])


@pytest.mark.parametrize("case", [1, 2, 3, 8, 9])
async def test_open_practice_vs_oracle(make_ctx: Any, raw: o.Raw, case: int) -> None:
    f, out = o.oracle_lms(raw, GOLDEN[case]), await _lms(await make_ctx(case))
    assert out.data["simulados_abertos"] == f.simulados_abertos
    assert out.data["simulados_com_tentativa_restante"] == f.simulados_com_tentativa_restante
    nxt = out.data["proxima_avaliacao"]
    assert (nxt["data"], nxt["tipo"], nxt["fonte"]) == (
        f.proxima_avaliacao,
        f.proxima_tipo,
        f.fonte,
    )


async def test_presencial_next_assessment_calendar(make_ctx: Any) -> None:
    out = await _lms(await make_ctx(1))  # presencial, sem prova futura no AVA
    assert out.data["proxima_avaliacao"] == {
        "data": "05/12/2026",
        "tipo": "AV2",
        "fonte": "calendario",
    }


async def test_no_next_assessment_says_so(make_ctx: Any) -> None:
    out = await _lms(await make_ctx(3), enrollment="2")  # transferida: sem provas futuras
    assert out.data["proxima_avaliacao"] is None
    assert "Não há próxima avaliação" in out.data["mensagem_proxima_avaliacao"]


async def test_no_lms_account(make_ctx: Any, loaded_db: Loaded) -> None:
    ctx = await make_ctx(1)
    with loaded_db.db.connect("postgres") as c:
        for t in ("lms_account", "lms_event", "lms_quiz", "lms_quiz_attempt"):
            c.execute(
                f"DELETE FROM canon.{t} WHERE enrollment_key = %s",
                (ctx.scope.enrollments[0].enrollment_key,),
            )  # type: ignore[call-overload]
    out = await _lms(ctx)
    assert "não tem conta no AVA" in out.data["mensagem"]
    assert "acessos_no_periodo" not in out.data and "simulados_abertos" not in out.data


async def test_lms_before_coverage(make_ctx: Any, raw: o.Raw) -> None:
    out = await _lms(await make_ctx(1), period="2022")
    assert "Não há dados do AVA antes de 15/01/2023." in json.dumps(out.data, ensure_ascii=False)
    assert "acessos_no_periodo" not in out.data  # nunca "0"
    cob = _fact(out, "cobertura")
    assert [(r.id, r.fields["desde"]) for r in cob] == [("lms", "15/01/2023")]
    assert cob[0].fields["desde"] == o.oracle_coverage(raw)["desde"]["lms"]
    assert cob[0].aliases == ("AVA", "LMS")


def _logins(raw: o.Raw, enr: o.Enr, start: date, end: date) -> int:
    uids = set(o._lms_index(raw)["by_enr"].get(enr, []))
    return sum(
        1 for e in raw["lms/mdl_logstore_standard_log"]
        if int(e["userid"]) in uids and e["eventname"] == "\\core\\event\\user_loggedin"
        and start <= datetime.fromtimestamp(int(e["timecreated"]), SP).date() <= end
    )  # fmt: skip


async def test_accesses_in_period(make_ctx: Any, raw: o.Raw) -> None:
    t = next(r for r in raw["sag/term"] if r["TERM"] == "2025/2")
    start, end = date.fromisoformat(t["START_DATE"]), date.fromisoformat(t["END_DATE"])
    want = _logins(raw, GOLDEN[2], start, end)
    assert want > 0
    out = await _lms(await make_ctx(2), period="2025/2")
    assert out.data["acessos_no_periodo"] == want and out.data["periodo"] == "2025/2"
    rec = _fact(out, "lms")[0]
    assert (rec.id, rec.aliases, rec.fields["acessos"]) == ("2025/2", ("2025/2",), str(want))


async def test_period_invalid(make_ctx: Any) -> None:
    from app.tools.base import ToolError

    with pytest.raises(ToolError) as e:
        await _lms(await make_ctx(1), period="ontem")
    assert e.value.code == "ARGUMENTO_INVALIDO"


async def test_event_no_ip_url(make_ctx: Any) -> None:
    out = await _lms(await make_ctx(2))
    blob = json.dumps(out.data, ensure_ascii=False) + repr(out.records)
    assert not re.search(
        r"\b\d{1,3}(\.\d{1,3}){3}\b|https?:|www\.|\bip\b|lms_user_id|attempt_id|quiz_id", blob, re.I
    )
    assert not re.search(r"\b\d{5,}\b", blob)  # nenhum id interno (RA/usuário/evento)


async def test_lms_output_trusted(make_ctx: Any, raw: o.Raw) -> None:
    out = await _lms(await make_ctx(2))
    assert out.untrusted is False
    onto = _onto()
    allowed = set(onto.subject_names_pt()) | {
        onto.room_subject(c["shortname"], None) for c in raw["lms/mdl_course"]
        if c["shortname"].startswith("INST-")
    }  # fmt: skip
    assert out.data["disciplinas"] and set(out.data["disciplinas"]) <= allowed
    assert {p["tipo"] for p in out.data["provas"]} <= {"AV1", "AV2", "simulado"}
    assert {p["situacao"] for p in out.data["provas"]} <= {
        "aberta",
        "realizada",
        "encerrada sem tentativa",
        "futura",
    }


async def test_rooms_without_idnumber_named(make_ctx: Any, raw: o.Raw) -> None:
    onto = _onto()
    ix = o._lms_index(raw)
    courses = {c["id"]: c for c in raw["lms/mdl_course"]}
    no_id = {i for i, c in courses.items() if not c["idnumber"]}
    hits = Counter(
        e["courseid"] for e in raw["lms/mdl_logstore_standard_log"]
        if e["courseid"] in no_id and int(e["userid"]) in set(ix["by_enr"].get(GOLDEN[2], []))
    )  # fmt: skip
    want = {onto.room_subject(courses[c]["shortname"], None) for c in hits} - {None}
    assert want, "premissa: golden 2 tem sala sem idnumber"
    out = await _lms(await make_ctx(2))
    assert want <= set(out.data["disciplinas"])


# ----------------------------------------------------------------------------- catálogo
def _prices(out: ToolOutput) -> dict[str, dict[str, Any]]:
    return {c["sigla"]: c for c in out.data["precos"]}


async def test_catalog_family(make_ctx: Any, raw: o.Raw) -> None:
    out = await _cat(await make_ctx(1), "preco")
    assert out.data["marca"] == "Uni São Paulo"
    own = o.oracle_catalog(raw, "UniSaoPaulo", "presencial")
    other = o.oracle_catalog(raw, "UniLitoral", "presencial")
    got = _prices(out)
    assert got
    for acr, c in own.items():
        if c.mensalidade:
            assert got[acr]["mensalidade"] == o.money(c.mensalidade)
    foreign = {o.money(c.mensalidade) for c in other.values() if c.mensalidade}
    mine = {o.money(c.mensalidade) for c in own.values() if c.mensalidade}
    assert not {c["mensalidade"] for c in got.values() if c["mensalidade"] in foreign - mine}
    assert "Litoral" not in json.dumps(out.data, ensure_ascii=False)


async def test_offer_by_modality_direito_ead_not_offered(make_ctx: Any, raw: o.Raw) -> None:
    assert not o.oracle_catalog(raw, "UniSaoPaulo", "ead")["DIR"].ofertado  # premissa do oráculo
    ctx = await make_ctx(1)
    ead = await _cat(ctx, "curso", query="Direito", modality="ead")
    assert [(c["curso"], c["oferta"]) for c in ead.data["cursos"]] == [("Direito", "não oferecido")]
    assert _fact(ead, "curso")[0].fields["oferta"] == "não oferecido"
    pres = await _cat(ctx, "curso", query="Direito", modality="presencial")
    want = o.oracle_catalog(raw, "UniSaoPaulo", "presencial")["DIR"]
    assert pres.data["cursos"][0]["oferta"] == ("oferecido" if want.ofertado else "não oferecido")


async def test_subject_na_sua_matriz(make_ctx: Any, raw: o.Raw) -> None:
    onto = _onto()
    cur = o.enrollments(raw)[GOLDEN[1]]["CURRICULUM_ID"]
    inside = {r["SUBJECT_CODE"] for r in raw["sag/curriculum_subject"] if r["CURRICULUM_ID"] == cur}
    code_in = sorted(inside)[0]
    code_out = next(c for c in sorted(onto._subjects) if c not in inside)
    ctx = await make_ctx(1)
    for code, want in ((code_in, True), (code_out, False)):
        name = onto.subject_name_pt(code)
        out = await _cat(ctx, "disciplina", query=name)
        row = next(d for d in out.data["disciplinas"] if d["disciplina"] == name)
        assert row["na_sua_matriz"] is want


async def test_get_catalog_subjects_calculo(make_ctx: Any) -> None:
    out = await _cat(await make_ctx(1), "disciplina", query="calculo")
    names = [d["disciplina"] for d in out.data["disciplinas"]]
    assert names and all("calculo" in unicodedata_norm(n) for n in names)
    assert out.data["total"] == len(names) and {r.kind for r in out.records} == {"disciplina"}


def unicodedata_norm(s: str) -> str:
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).casefold()


async def test_price_fields(make_ctx: Any, raw: o.Raw) -> None:
    out = await _cat(await make_ctx(1), "preco")
    got = _prices(out)
    want = o.oracle_catalog(raw, "UniSaoPaulo", "presencial")
    for acr, c in want.items():
        if c.mensalidade:
            g = got[acr]
            assert (g["mensalidade"], g["ano"], g["parcelas_por_semestre"]) == (
                o.money(c.mensalidade), c.ano, c.parcelas_por_semestre,
            )  # fmt: skip
    rec = _fact(out, "curso")[0]
    assert {"mensalidade", "ano", "parcelas_por_semestre"} <= set(rec.fields)


async def test_calendar_av_dates(make_ctx: Any) -> None:
    out = await _cat(await make_ctx(1), "calendario", query="2026/2")
    assert out.data["periodos"] == [
        {"periodo": "2026/2", "inicio": "01/08/2026", "fim": "15/12/2026",
         "av1": "03/10/2026", "av2": "05/12/2026", "atual": True}
    ]  # fmt: skip
    rec = _fact(out, "calendario")[0]
    assert (rec.id, rec.fields["av1"], rec.fields["av2"]) == ("2026/2", "03/10/2026", "05/12/2026")


async def test_services_orientation(make_ctx: Any) -> None:
    onto = _onto()
    out = await _cat(await make_ctx(1), "servico")
    by_id = {r.id: r for r in _fact(out, "servico")}
    orient = [c for c in onto.service_codes() if onto.service(c).mode == "orientation"]
    assert len(orient) == 4
    for code in orient:
        rec = by_id[code]
        assert dict(rec.fields) == orientation(code)
        assert {"orientacao", "taxa", "prazo"} <= set(rec.fields)
        assert rec.fields["porque_agente_nao_executa"]
    assert (
        "GEN-090" in by_id
        and onto.service("GEN-090").ui_label in by_id["GEN-090"].fields["orientacao"]
    )
    assert not re.search(
        r"\b[A-Z]{3}-\d{3}\b", json.dumps(out.data, ensure_ascii=False)
    )  # códigos → nomes PT


@pytest.mark.parametrize(
    "kind", ["curso", "grade", "disciplina", "calendario", "unidade", "servico", "preco"]
)
async def test_all_catalog_kinds(make_ctx: Any, kind: str) -> None:
    out = await _cat(await make_ctx(1), kind)
    assert out.untrusted is False and out.data
    assert len(json.dumps(out.data)) > 20


async def test_catalog_unknown_kind(make_ctx: Any) -> None:
    from app.tools.base import ToolError

    with pytest.raises(ToolError):
        await _cat(await make_ctx(1), "segredos")


# ----------------------------------------------------------------------------- fix round 1
@pytest.mark.parametrize(
    "args",
    [{"period": 2026}, {"enrollment": "9"}, {"enrollment": 1}, {"extra": "x"}],
)
async def test_lms_bad_args_tool_error(make_ctx: Any, args: dict[str, Any]) -> None:
    from app.tools.base import ToolError

    with pytest.raises(ToolError) as e:
        await lms.TOOLS[0].handler(await make_ctx(1), args)
    assert e.value.code == "ARGUMENTO_INVALIDO"


@pytest.mark.parametrize(
    "args",
    [{}, {"kind": "preco", "modality": "hibrido"}, {"kind": "curso", "query": 5},
     {"kind": ["curso"]}, {"kind": "curso", "enrollment": "7"}],
)  # fmt: skip
async def test_catalog_bad_args_tool_error(make_ctx: Any, args: dict[str, Any]) -> None:
    from app.tools.base import ToolError

    with pytest.raises(ToolError) as e:
        await catalog.TOOLS[0].handler(await make_ctx(1), args)
    assert e.value.code == "ARGUMENTO_INVALIDO"


@pytest.mark.parametrize("period", ["2022", "2027/1"])  # antes da cobertura | sem login
async def test_lms_record_never_zero_accesses(make_ctx: Any, period: str) -> None:
    out = await _lms(await make_ctx(1), period=period)
    assert "acessos_no_periodo" not in out.data and out.data["mensagem_acessos"]
    assert "acessos" not in _fact(out, "lms")[0].fields


async def test_lms_nullable_columns(make_ctx: Any, loaded_db: Loaded) -> None:
    from app.tools.base import find_entity, index_records

    ctx = await make_ctx(2)
    ek = ctx.scope.enrollments[0].enrollment_key
    base = await _lms(ctx)
    with loaded_db.db.connect("postgres") as c:
        c.execute(  # type: ignore[call-overload]
            "UPDATE canon.lms_quiz SET subject_name_pt = NULL WHERE enrollment_key = %s"
            " AND quiz_id IN (SELECT quiz_id FROM canon.lms_quiz WHERE enrollment_key = %s"
            " ORDER BY quiz_id LIMIT 3)", (ek, ek),
        )  # fmt: skip
        c.execute(  # type: ignore[call-overload]
            "UPDATE canon.lms_quiz SET opens_at = NULL WHERE enrollment_key = %s AND quiz_id ="
            " (SELECT max(quiz_id) FROM canon.lms_quiz WHERE enrollment_key = %s)", (ek, ek),
        )  # fmt: skip
        c.execute(  # type: ignore[call-overload]
            "UPDATE canon.lms_quiz SET closes_at = NULL WHERE enrollment_key = %s AND quiz_id ="
            " (SELECT min(quiz_id) FROM canon.lms_quiz WHERE enrollment_key = %s)", (ek, ek),
        )  # fmt: skip
    out = await _lms(ctx)
    assert out.data["total_provas"] == base.data["total_provas"] - 2  # datas nulas: ignoradas
    assert all(isinstance(a, str) and a for r in out.records for a in r.aliases)
    idx = index_records(out.records)
    for p in out.data["provas"]:
        assert find_entity(idx, p["disciplina"]) is not None


async def test_lms_truncation_warning(make_ctx: Any) -> None:
    out = await _lms(await make_ctx(2))
    assert out.data["total_provas"] > 20 and len(out.data["provas"]) == 20
    assert (
        "20" in out.data["aviso_limite"]
        and str(out.data["total_provas"]) in out.data["aviso_limite"]
    )


async def test_catalog_truncation_warning(make_ctx: Any) -> None:
    out = await _cat(await make_ctx(1), "disciplina")
    assert out.data["total"] > 20 and len(out.data["disciplinas"]) == 20
    assert str(out.data["total"]) in out.data["aviso_limite"]
    small = await _cat(await make_ctx(1), "disciplina", query="calculo")
    assert "aviso_limite" not in small.data


async def test_offered_course_without_price_row(make_ctx: Any, loaded_db: Loaded) -> None:
    ctx = await make_ctx(1)
    ok = await _cat(ctx, "preco")
    acr = ok.data["precos"][0]["sigla"]
    with loaded_db.db.connect("postgres") as c:
        c.execute(  # type: ignore[call-overload]
            "DELETE FROM canon.price WHERE brand_family = 'UniSaoPaulo' AND modality = 'CAMPUS'"
            " AND course_id = (SELECT course_id FROM canon.course WHERE acronym = %s)", (acr,),
        )  # fmt: skip
    out = await _cat(ctx, "preco")
    row = next(r for r in out.data["precos"] if r["sigla"] == acr)
    assert row["oferta"] == "oferecido" and "mensalidade" not in row
    assert "Preço não disponível" in row["mensagem_preco"]
    rec = next(r for r in _fact(out, "curso") if r.id == acr)
    assert rec.fields["preco"] == "indisponível"


_KIND_KEY = {"curso": "cursos", "grade": "disciplinas", "disciplina": "disciplinas",
             "calendario": "periodos", "unidade": "unidades", "servico": "servicos",
             "preco": "precos"}  # fmt: skip


@pytest.mark.parametrize("kind", list(_KIND_KEY))
async def test_every_kind_has_populated_list(make_ctx: Any, kind: str) -> None:
    out = await _cat(await make_ctx(1), kind)
    items = out.data[_KIND_KEY[kind]]
    assert items and out.data["total"] >= len(items) and out.data["marca"] == "Uni São Paulo"
    assert "mensagem" not in out.data
    if kind != "unidade":
        assert out.records
