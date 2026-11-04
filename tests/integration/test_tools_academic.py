from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from app.config import AppSettings
from app.data.db import create_pool, load_scope, with_scope
from app.infra.stores.labels import LabelCache
from app.tools.academic import TOOLS
from app.tools.base import ToolContext, ToolOutput
from core.format import fmt_grade, fmt_pct
from evals import oracle
from tests.conftest import GOOD
from tests.integration.conftest import MINI, Loaded
from tests.unit.redis_fake import FakeRedis

pytestmark = pytest.mark.asyncio(loop_scope="function")
RAW = oracle.load_csv(MINI)
BY_NAME = {t.spec.name: t for t in TOOLS}
G1 = ("UniSaoPaulo", "181722")
G4 = ("UniSaoPaulo", "180823")
G9 = ("UniSaoPaulo", "182915")
G9_GRAD = ("UniSaoPaulo_EAD", "011843")
G3 = ("UniSaoPaulo_EAD", "014214")


@pytest.fixture
async def pool(loaded_db: Loaded) -> AsyncIterator:  # type: ignore[type-arg]
    pw = (loaded_db.db.secrets / "agent_app_password").read_text().strip()
    s = AppSettings(
        **{**GOOD, "agent_app_password": pw},  # type: ignore[arg-type]
        db_host="127.0.0.1", db_port=int(loaded_db.db.port), db_name=loaded_db.db.name,
    )  # fmt: skip
    p = create_pool(s)
    await p.open()
    p.settings = s  # type: ignore[attr-defined]
    try:
        yield p
    finally:
        await p.close()


def _person(loaded: Loaded, enr: tuple[str, str]) -> UUID:
    with loaded.db.connect("postgres") as c:
        row = c.execute(
            "SELECT person_key FROM canon.enrollment WHERE ra = %s AND brand = %s", enr[::-1]
        ).fetchone()
    assert row
    return row[0]


def _index(loaded: Loaded, scope, enr: tuple[str, str]) -> str:  # type: ignore[no-untyped-def]
    with loaded.db.connect("postgres") as c:
        row = c.execute(
            "SELECT enrollment_key FROM canon.enrollment WHERE ra = %s AND brand = %s", enr[::-1]
        ).fetchone()
    return next(e.index for e in scope.enrollments if e.enrollment_key == row[0])  # type: ignore[index]


async def call(  # type: ignore[no-untyped-def]
    pool,
    loaded: Loaded,
    enr: tuple[str, str],
    tool: str,
    args: dict | None = None,
    *,
    offer: bool = True,
    on: tuple[str, str] | None = None,
) -> ToolOutput:
    pk = _person(loaded, enr)
    async with with_scope(pool, pk, ()) as c:
        scope = await load_scope(c, pk, LabelCache(FakeRedis()))
    args = dict(args or {})
    if on is not None:
        args["enrollment"] = _index(loaded, scope, on)
    elif len(scope.enrollments) > 1 or "enrollment" in args:
        args.setdefault("enrollment", _index(loaded, scope, enr))
    ctx = ToolContext(
        scope, pool.settings, "web", "c1", False, 1, offer, date(2026, 10, 31),
        lambda: with_scope(pool, pk, scope.brand_families), None,  # type: ignore[arg-type]
    )  # fmt: skip
    return await BY_NAME[tool].handler(ctx, args)


async def test_profile_golden1(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "get_academic_profile")
    c = oracle.oracle_courses(RAW, G1)
    assert out.data["status"].casefold() == oracle.ENROLLMENT_STATUS_PT[c.status]
    assert out.data["conclusao"] == oracle.fmt_decimal1(c.conclusao_pct) + "%"
    assert out.data["cr"] == oracle.fmt_decimal1(c.cr)  # type: ignore[arg-type]
    assert out.data["semestre"] == str(c.semestre_nivel)
    assert out.data["curso"]
    assert out.data["matriz"] == "CON-2021"
    assert out.data["ingresso"] == "14/01/2025"
    assert out.data["historico"][0]["motivo"] == "Nova matrícula"
    assert "matricula" not in out.data  # uma só matrícula: não diz de qual fala


async def test_golden9_documents(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G9, "get_academic_profile", on=G9_GRAD)
    links = oracle.oracle_link_protocols(RAW)
    want = {
        oracle.br_date(oracle._iso_date(d["Issued_At"]))
        for d in RAW["secretary/issued_document"]
        if d["Document_Type"] == "Diploma" and links[d["Protocol"]].enrollment == G9_GRAD
    }
    assert want
    diplomas = [d for d in out.data["documentos"] if d["tipo"] == "Diploma"]
    assert {d["emitido_em"] for d in diplomas} == want
    assert any(r.kind == "documento" and "Diploma" in r.aliases for r in out.records)
    assert out.data["status"] == "Formada"


async def test_status_history_golden3(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G3, "get_academic_profile", on=("UniSaoPaulo", "181544"))
    want = oracle.oracle_enrollment_events(RAW, ("UniSaoPaulo", "181544"))
    assert [h["data"] for h in out.data["historico"]] == [w["data"] for w in want]
    assert [h["para"] for h in out.data["historico"]] == ["Ativa", "Transferida"]
    last = out.data["historico"][-1]
    assert (last["de"], last["para"], last["motivo"]) == (
        "Ativa",
        "Transferida",
        "Transferência interna",
    )
    first = (await call(pool, loaded_db, G3, "get_academic_profile", on=G3)).data["historico"][0]
    assert first["motivo"] == "Transferência interna"


async def test_cancelled_over_registered(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    enr = ("UniAraucaria_EAD", "012815")
    regs = oracle.oracle_term_registration(RAW, enr)
    assert regs[-1]["status"] == "REGISTERED"  # premissa dos dados
    out = await call(pool, loaded_db, enr, "get_academic_profile", on=enr)
    assert out.data["status"] == "Cancelada"
    assert "rematricula" not in out.data
    assert "prevalece" in out.data["observacao_rematricula"]


async def test_subject_match_pt_prefix_multi(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "list_course_results", {"subject": "contabilidade"})
    names = {d["disciplina"] for d in out.data["disciplinas"]}
    assert {
        "Contabilidade Financeira I",
        "Contabilidade de Custos I",
        "Introdução à Contabilidade",
    } <= names
    one = await call(
        pool, loaded_db, G1, "list_course_results", {"subject": "CONTABILIDADE financeira"}
    )
    assert {d["disciplina"] for d in one.data["disciplinas"]} == {"Contabilidade Financeira I"}


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_subject_match_calculo() -> None:  # a fixture não tem Cálculo: regra pura
    from app.tools.academic import _subject_filter

    names = ("Cálculo I", "Cálculo II", "Cálculo III", "Álgebra")
    rows = [{"subject_code": c, "n": n} for c, n in zip("ABCD", names, strict=True)]
    assert [r["n"] for r in _subject_filter("cálculo", rows, "n")] == [
        "Cálculo I",
        "Cálculo II",
        "Cálculo III",
    ]
    assert [r["n"] for r in _subject_filter("calculo ii", rows, "n")] == ["Cálculo II"]
    assert [r["n"] for r in _subject_filter("alg", rows, "n")] == ["Álgebra"]


async def test_subject_not_in_curriculum(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "list_course_results", {"subject": "Anatomia"})
    assert "não faz parte da sua matriz" in out.data["aviso"]
    assert out.data["disciplinas"] == []


async def test_attendance(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "list_course_results", {"term": "2025/1"})
    want = {
        d["disciplina"]: (fmt_pct(Decimal(d["frequencia"])), fmt_grade(Decimal(d["final"])))
        for d in oracle.oracle_courses(RAW, G1).disciplinas
        if d["semestre"] == "2025/1"
    }
    got = {d["codigo"]: (d["frequencia"], d["media_final"]) for d in out.data["disciplinas"]}
    assert got == want and want
    assert out.data["total"] == len(want)


async def test_divergence_flag_surfaced(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    with loaded_db.db.connect("postgres") as c:  # a fixture não traz divergência: cria uma
        c.execute(
            "UPDATE canon.class_result SET divergencia = true, attendance_pct = 80"
            " WHERE subject_code = 'CON0103' AND enrollment_key = (SELECT enrollment_key"
            " FROM canon.enrollment WHERE ra = '181722' AND brand = 'UniSaoPaulo')"
        )
    out = await call(
        pool, loaded_db, G1, "list_course_results", {"subject": "Introdução à Contabilidade"}
    )
    d = out.data["disciplinas"][0]
    assert d["divergencia"].startswith("sim")
    rec = next(r for r in out.records if r.id == "CON0103")
    assert rec.fields["divergencia"] == d["divergencia"]
    other = await call(
        pool, loaded_db, G1, "list_course_results", {"subject": "contabilidade financeira"}
    )
    assert all("divergencia" not in x for x in other.data["disciplinas"])


@pytest.mark.parametrize(("enr", "want"), [(G1, "2028/2"), (G4, "2028/1")])
async def test_progress_forecast(pool, loaded_db: Loaded, enr, want) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, enr, "get_curriculum_progress")
    assert oracle.oracle_forecast(RAW, enr).previsao == want
    assert out.data["previsao"] == f"{want}, se cursar regularmente"
    assert (
        out.data["pendentes_total"] == len(out.data["pendentes"])
        or out.data["pendentes_total"] > 20
    )
    assert all(p["situacao"] in ("pendente", "cursando") for p in out.data["pendentes"])


async def test_progress_no_forecast_when_not_active(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G9, "get_curriculum_progress", on=G9_GRAD)
    assert "previsao" not in out.data
    assert out.data["pendentes_total"] == 0 or out.data["pendentes"]


async def test_full_list_for_term(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G4, "list_course_results", {"term": "2026/1"})
    want = [d for d in oracle.oracle_courses(RAW, G4).disciplinas if d["semestre"] == "2026/1"]
    assert 0 < len(want) <= 20
    assert out.data["total"] == len(want) == len(out.data["disciplinas"])
    assert all(d["semestre"] == "2026/1" for d in out.data["disciplinas"])


async def test_result_filter(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "list_course_results", {"result": "cursando"})
    want = [
        d for d in oracle.oracle_courses(RAW, G1).disciplinas if d["resultado"] == "IN_PROGRESS"
    ]
    assert out.data["total"] == len(want)


async def test_sag_coverage_note(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "list_course_results", {"term": "2019/1"})
    assert out.data["disciplinas"] == []
    assert "Não há dados do SAG antes de 01/02/2021." in out.data["aviso"]
    assert any(
        r.kind == "cobertura" and r.id == "sag" and r.fields["desde"] == "01/02/2021"
        for r in out.records
    )


async def test_single_enrollment_no_other_field(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "get_academic_profile")
    assert "outras_matriculas" not in out.data


async def test_other_enrollment_field_only_when_offered(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    on = await call(pool, loaded_db, G9, "get_academic_profile", offer=True)
    assert len(on.data["outras_matriculas"]) == 1
    assert on.data["matricula"]
    off = await call(pool, loaded_db, G9, "get_academic_profile", offer=False)
    assert "outras_matriculas" not in off.data
    assert off.data["matricula"]  # com 2 matrículas diz de qual fala


async def test_records_are_entity_records(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    from app.tools.base import find_entity, index_records

    out = await call(pool, loaded_db, G1, "list_course_results", {"term": "2025/1"})
    idx = index_records(out.records)
    for d in out.data["disciplinas"]:
        r = find_entity(idx, d["disciplina"])
        assert r is not None and r.kind == "disciplina" and r.id == d["codigo"]
        assert find_entity(idx, d["disciplina"].upper()) is r
    prof = await call(pool, loaded_db, G1, "get_academic_profile")
    assert any(r.kind == "matricula" and r.id == "1" for r in prof.records)


async def test_no_raw_ids_leak(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    out = await call(pool, loaded_db, G1, "list_course_results")
    assert "class_id" not in str(out.data) and "enrollment_key" not in str(out.data)
    assert out.data["total"] > len(out.data["disciplinas"]) or out.data["total"] <= 20


async def test_invalid_enrollment(pool, loaded_db: Loaded) -> None:  # type: ignore[no-untyped-def]
    from app.tools.base import ToolError

    with pytest.raises(ToolError):
        await call(pool, loaded_db, G1, "get_academic_profile", {"enrollment": "3"})
    with pytest.raises(ToolError):
        await call(pool, loaded_db, G1, "get_academic_profile", {"enrollment": "9"})
