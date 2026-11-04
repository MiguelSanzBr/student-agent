"""Todo fato que a ferramenta entrega ao LLM (`data`) tem de estar nos `records` (evidência).

Sem banco: as consultas de cada módulo são trocadas por fakes. A cobertura é conferida com o
tokenizador da guarda (só leitura) e a aceitação, com `check_evidence` (sem alterá-la).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.config import AppSettings
from app.guards.evidence import check_evidence
from app.guards.tokens import tokenize
from app.rules.eligibility import Eligible
from app.rules.finance import LateCharge, Payment
from app.tools import academic, catalog, finance, lms, protocols
from app.tools.base import ToolContext, ToolOutput, index_records
from core.ontology import Ontology
from core.scope import EnrollmentRef, StudentScope

pytestmark = pytest.mark.asyncio(loop_scope="function")
ONTO = Ontology.load(Path("ontology"))
TODAY = date(2026, 10, 31)
SP_NOW = datetime(2026, 10, 30, 14, 5, tzinfo=UTC)


def _ref(i: str, label: str, status: str = "ACTIVE") -> EnrollmentRef:
    return EnrollmentRef(i, uuid4(), label, "UniSaoPaulo", "UniSaoPaulo",  # type: ignore[arg-type]
                         "presencial", status, i == "1")  # fmt: skip


SCOPE = StudentScope(uuid4(), (_ref("1", "Administração"),), ("UniSaoPaulo",))
SCOPE2 = StudentScope(uuid4(), (_ref("1", "Administração"), _ref("2", "Direito")), ("UniSaoPaulo",))


class _Drafts:
    async def same_pending(self, *_: Any) -> None:
        return None

    async def put(self, *_: Any) -> None:
        return None


def ctx(scope: StudentScope = SCOPE) -> ToolContext:
    @asynccontextmanager
    async def db() -> Any:
        yield None

    return ToolContext(scope, AppSettings.model_construct(), "web", "c1", False, 1, True, TODAY,
                       db, _Drafts())  # type: ignore[arg-type]  # fmt: skip


def _leaves(v: Any) -> Iterator[str]:
    if isinstance(v, Mapping):
        for x in v.values():
            yield from _leaves(x)
    elif isinstance(v, list | tuple):
        for x in v:
            yield from _leaves(x)
    elif isinstance(v, str | int | Decimal):
        yield str(v)


def uncovered(out: ToolOutput) -> list[str]:
    """Fatos (números, datas, períodos, valores) do `data` que nenhum registro sustenta."""
    have: set[str] = set()
    for r in out.records:
        for t in (r.id, *r.aliases, *r.fields.values()):
            have |= {k.norm for k in tokenize(t)}
    return sorted(
        {f"{leaf!r}: {k.raw}" for leaf in _leaves(out.data) for k in tokenize(leaf)
         if k.kind not in ("ord",) and k.norm not in have}
    )  # fmt: skip


def accepted(out: ToolOutput, answer: str, *, indices: tuple[str, ...] = ("1",)) -> bool:
    r = check_evidence(
        answer, question="", records_by_entity=index_records(out.records), card_facts={},
        enrollment_indices=indices, tool_calls_this_turn=(), system_notes=(), canary="c" * 32,
        prompt_prefix="Você é o assistente de atendimento ao aluno e responde só com dados.",
        ontology=ONTO,
    )  # fmt: skip
    assert r.ok, r.failures
    return r.ok


# ---------------------------------------------------------------- acadêmico


def _enr_row(**kw: Any) -> dict[str, Any]:
    return {
        "ra": "1", "status": "ACTIVE", "status_date": date(2025, 2, 3), "unit_label": "Campus X",
        "modality": "CAMPUS", "shift": "NIGHT", "entry_type": "VESTIBULAR",
        "enrolled_on": date(2024, 2, 1), "entry_term": "2024/1", "semester": Decimal(5),
        "completion_pct": Decimal("34.8"), "gpa": Decimal("7.5"), "origin_brand": None,
        "curriculum_id": "ADM-2020", "course_id": "ADM", "course_name": "Administração",
        "duration_semesters": 8, **kw,
    }  # fmt: skip


def _grid(n: int = 32) -> list[dict[str, Any]]:
    return [
        {"subject_code": f"ADM{i:03d}", "name_pt": f"Matéria {chr(65 + i % 26)}{i}",
         "hours": Decimal(100 if i < 8 else 50), "semester": Decimal(i // 4 + 1),
         "mandatory": True, "prerequisite_code": "ADM000" if i == 20 else None,
         "prerequisite_name": "Base" if i == 20 else None}
        for i in range(n)
    ]  # fmt: skip


def _patch_academic(mp: pytest.MonkeyPatch, *, done: int = 12, grid: int = 32) -> None:
    async def enrollment(*_: Any) -> dict[str, Any]:
        return _enr_row()

    async def events(*_: Any) -> list[dict[str, Any]]:
        return [
            {"term": "2025/1", "event_date": date(2025, 2, 3), "old_status": "ACTIVE",
             "new_status": "LOCKED", "reason": "FINANCIAL"},
            {"term": "2025/2", "event_date": date(2025, 8, 4), "old_status": "LOCKED",
             "new_status": "ACTIVE", "reason": "RETURN"},
        ]  # fmt: skip

    async def regs(*_: Any) -> list[dict[str, Any]]:
        return [{"term": "2026/2", "term_status": "REGISTERED", "registration_date": TODAY}]

    async def docs(*_: Any) -> list[dict[str, Any]]:
        return [{"document_id": "D1", "document_type": "Transcript", "issued_at": date(2026, 3, 9),
                 "delivery": "Digital"}]  # fmt: skip

    async def results(*_: Any) -> list[dict[str, Any]]:
        return [
            {"subject_code": f"ADM{i:03d}", "subject_name_pt": f"Matéria {chr(65 + i % 26)}{i}",
             "term": "2025/1", "result": "APPROVED", "grade_av1": Decimal(8), "grade_av2": None,
             "grade_av3": None, "final_grade": Decimal("8.5"), "attendance_pct": Decimal(90),
             "professor": None, "divergencia": False}
            for i in range(done)
        ]  # fmt: skip

    async def sag(*_: Any) -> date:
        return date(2023, 1, 15)

    async def curriculum(*_: Any) -> list[dict[str, Any]]:
        return _grid(grid)

    async def hours(*_: Any) -> Decimal:
        return Decimal(2300)

    async def term(*_: Any) -> str:
        return "2026/2"

    for name, fn in (("enrollment", enrollment), ("events", events), ("term_registrations", regs),
                     ("documents", docs), ("results", results), ("sag_coverage", sag),
                     ("curriculum", curriculum), ("curriculum_hours", hours),
                     ("current_term", term)):  # fmt: skip
        mp.setattr(academic.q, name, fn)


async def test_progress_covers_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_academic(monkeypatch)
    out = await academic.get_curriculum_progress(ctx(), {})
    assert uncovered(out) == []
    d = out.data
    accepted(
        out,
        f"Você concluiu {d['disciplinas_cumpridas']} de {d['disciplinas_na_matriz']} disciplinas, "
        f"{d['carga_cumprida_horas']} de {d['carga_total_horas']} horas ({d['conclusao']}). "
        f"Previsão de conclusão: {d['previsao'].split(',')[0]}.",
    )


async def test_progress_truncated_notice_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_academic(monkeypatch, done=5, grid=40)
    out = await academic.get_curriculum_progress(ctx(), {})
    assert "aviso_limite" in out.data
    assert uncovered(out) == []


async def test_profile_covers_history(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_academic(monkeypatch)
    out = await academic.get_academic_profile(ctx(), {})
    assert uncovered(out) == []
    accepted(
        out, "Na matrícula 1 houve mudança de situação em 03/02/2025 (2025/1) e em 04/08/2025."
    )


async def test_results_total_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_academic(monkeypatch, done=25)
    out = await academic.list_course_results(ctx(), {})
    assert out.data["total"] == 25 and "aviso_limite" in out.data
    assert uncovered(out) == []
    accepted(out, "Você tem 25 resultados registrados; mostrei os 20 mais recentes de 25.")


# ---------------------------------------------------------------- financeiro


def _patch_finance(mp: pytest.MonkeyPatch, *, covered_since: date | None = None) -> None:
    inv = [
        finance.q.LedgerInvoice("F1", 50000, "OPEN", TODAY + timedelta(days=5), "2026-11"),
        finance.q.LedgerInvoice("F2", 30000, "OPEN", TODAY - timedelta(days=40), "2026-09"),
    ]

    async def ledger(*_: Any) -> tuple[list[Any], list[Payment], list[LateCharge]]:
        return inv, [], [LateCharge("F2", 1234)]

    async def disc(*_: Any) -> list[tuple[Any, ...]]:
        return [("D1", "MERIT", Decimal("0.2"), None, None)]

    async def agr(*_: Any) -> list[tuple[Any, ...]]:
        return [("AG2026000340", "ACTIVE", 10, 100000, 5000, 95000, date(2026, 9, 1), 2)]

    async def payer(*_: Any) -> str:
        return "STUDENT"

    async def cov(*_: Any) -> date | None:
        return covered_since

    mp.setattr(finance.q, "fetch_ledger", ledger)
    mp.setattr(finance.q, "fetch_discounts", disc)
    mp.setattr(finance.q, "fetch_agreements", agr)
    mp.setattr(finance.q, "fetch_payer_type", payer)
    mp.setattr(finance.q, "fetch_coverage_start", cov)


async def test_finance_summary_covers_days(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_finance(monkeypatch)
    out = await finance.get_financial_summary(ctx(), {})
    assert uncovered(out) == []
    accepted(out, "A próxima fatura vence em 5 dias, em 05/11/2026.")


async def test_finance_summary_two_enrollments_total_geral(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_finance(monkeypatch)
    out = await finance.get_financial_summary(ctx(SCOPE2), {})
    assert uncovered(out) == []
    accepted(out, f"Somando as matrículas, o total geral é {out.data['total_geral']}.",
             indices=("1", "2"))  # fmt: skip


async def test_invoices_cover_valor_total_and_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_finance(monkeypatch, covered_since=date(2024, 1, 1))
    out = await finance.list_invoices(ctx(), {"period": "2023-05"})
    assert "nota" in out.data
    # lista vazia: o "0" é tratado pela guarda via chamada vazia (returned_records == 0)
    assert uncovered(out) == ["'0': 0"]
    out = await finance.list_invoices(ctx(), {})
    assert uncovered(out) == []
    accepted(out, "Você tem 2 faturas. A de 2026-11 tem valor de R$ 500,00.")


async def test_agreements_cover_total(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_finance(monkeypatch)
    out = await finance.list_agreements(ctx(), {})
    assert uncovered(out) == []
    accepted(out, "Você tem 1 acordo.")


# ---------------------------------------------------------------- LMS


def _patch_lms(mp: pytest.MonkeyPatch, n_rooms: int = 22) -> None:
    async def window(*_: Any) -> tuple[str, date, date]:
        return "2026/2", date(2026, 7, 1), date(2026, 12, 31)

    async def cov(*_: Any) -> date:
        return date(2023, 1, 15)

    async def last(*_: Any) -> tuple[int, datetime]:
        return 1, SP_NOW

    async def rooms(*_: Any) -> list[str]:
        return [f"Sala {chr(65 + i)}" for i in range(n_rooms)]

    async def quizzes(*_: Any) -> list[tuple[Any, ...]]:
        return [("Q1", "prova", SP_NOW, SP_NOW + timedelta(days=20), 1, "Matéria A")]

    async def attempts(*_: Any) -> list[str]:
        return []

    async def cal(*_: Any) -> dict[str, date]:
        return {}

    async def logins(*_: Any) -> int:
        return 7

    for name, fn in (("term_window", window), ("lms_coverage", cov), ("last_access", last),
                     ("rooms", rooms), ("quizzes", quizzes), ("attempts", attempts),
                     ("calendar", cal), ("logins", logins)):  # fmt: skip
        mp.setattr(lms.q, name, fn)


async def test_lms_covers_totals_and_rooms(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_lms(monkeypatch)
    out = await lms.get_lms_activity(ctx(), {})
    assert uncovered(out) == []
    names = {a for r in out.records for a in r.aliases}
    assert set(out.data["disciplinas"]) <= names  # a guarda só aceita disciplina com registro
    accepted(out, "Você tem 22 disciplinas no AVA e 1 prova, com 7 acessos em 2026/2.")


# ---------------------------------------------------------------- catálogo


def _patch_catalog(mp: pytest.MonkeyPatch, n: int = 25) -> None:
    async def courses(*_: Any) -> list[tuple[Any, ...]]:
        return [(f"C{i}", f"AC{i}", f"Curso {i}", "BACHAREL") for i in range(n)]

    async def offers(*_: Any) -> dict[str, list[str]]:
        return {f"C{i}": ["Campus Sul"] for i in range(n)}

    async def prices(*_: Any) -> dict[str, tuple[int, int, int]]:
        return {f"C{i}": (89900, 2026, 6) for i in range(n)}

    async def cur_of(*_: Any) -> tuple[str, str]:
        return "ADM-2020", "ADM"

    async def subjects(*_: Any) -> list[tuple[Any, ...]]:
        return [(f"S{i}", f"Disc {i}", Decimal(60), Decimal(4), Decimal(2), True) for i in range(n)]

    async def terms(*_: Any) -> list[tuple[Any, ...]]:
        return [(f"2026/{h}", date(2026, 2, 1), date(2026, 6, 30), date(2026, 4, 1),
                 date(2026, 5, 20), h == 2) for h in (1, 2)]  # fmt: skip

    async def units(*_: Any) -> list[tuple[Any, ...]]:
        return [("Campus Sul", "CAMPUS", "SP"), ("Polo Norte", "POLO", "RJ")]

    for name, fn in (("courses", courses), ("offers", offers), ("prices", prices),
                     ("curriculum_of", cur_of), ("subjects", subjects), ("terms", terms),
                     ("units", units)):  # fmt: skip
        mp.setattr(catalog.q, name, fn)


@pytest.mark.parametrize("kind", ["curso", "preco", "disciplina", "grade", "calendario",
                                  "unidade", "servico"])  # fmt: skip
async def test_catalog_covers(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    _patch_catalog(monkeypatch)
    out = await catalog.get_catalog(ctx(), {"kind": kind})
    assert uncovered(out) == []


async def test_catalog_units_have_records(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_catalog(monkeypatch)
    out = await catalog.get_catalog(ctx(), {"kind": "unidade"})
    assert {a for r in out.records for a in r.aliases} >= {"Campus Sul", "Polo Norte"}
    accepted(out, "O Polo Norte fica em RJ.")


async def test_catalog_current_term_flag_and_total(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_catalog(monkeypatch)
    out = await catalog.get_catalog(ctx(), {"kind": "calendario"})
    accepted(out, "O período 2026/2 vai de 01/02/2026 a 30/06/2026 e a AV1 é em 01/04/2026.")
    out = await catalog.get_catalog(ctx(), {"kind": "curso"})
    accepted(out, "Encontrei 25 cursos; mostrei 20.")


# ---------------------------------------------------------------- protocolos


def _patch_protocols(mp: pytest.MonkeyPatch) -> None:
    async def sec(*_: Any) -> list[tuple[Any, ...]]:
        return [("20260022332", "DOC-001", "OPEN", SP_NOW, "responsavel")]

    async def sa(*_: Any) -> list[tuple[Any, ...]]:
        return [("SA-2026-000123", "DOC-001", TODAY, TODAY + timedelta(days=2))]

    async def cov(*_: Any) -> date:
        return date(2024, 1, 1)

    async def one(*_: Any) -> tuple[Any, ...]:
        return ("20260022332", "DOC-001", "OPEN", SP_NOW, "aluno")

    async def events(*_: Any) -> list[tuple[Any, ...]]:
        return [(None, "OPEN", SP_NOW), ("OPEN", "IN_PROGRESS", SP_NOW + timedelta(hours=3))]

    async def msgs(*_: Any) -> list[tuple[Any, ...]]:
        return [("Analyst", "Estamos analisando o seu pedido.", None, SP_NOW)]

    async def docs(*_: Any) -> list[tuple[Any, ...]]:
        return [("Transcript", SP_NOW, "Digital")]

    async def inv(*_: Any) -> tuple[Any, ...]:
        return ("F9", "2026-10", "OPEN", date(2026, 10, 10), 45000)

    async def term(*_: Any) -> str:
        return "2026/2"

    async def elig(*_: Any, **__: Any) -> Eligible:
        return Eligible()

    for name, fn in (("list_secretary", sec), ("list_sa", sa), ("coverage_start", cov),
                     ("secretary_protocol", one), ("events", events), ("messages", msgs),
                     ("documents", docs), ("linked_invoice", inv),
                     ("current_term", term)):  # fmt: skip
        mp.setattr(protocols.q, name, fn)
    mp.setattr(protocols, "check_eligibility", elig)


async def test_protocol_list_covers(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_protocols(monkeypatch)
    out = await protocols._list(ctx(), {})
    assert uncovered(out) == []
    accepted(out, "Você tem 2 protocolos. O 20260022332 foi aberto pelo responsável.")


async def test_protocol_get_covers_history_documents_invoice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_protocols(monkeypatch)
    out = await protocols._get(ctx(), {"numero": "20260022332"})
    assert uncovered(out) == []
    accepted(out, "O protocolo 20260022332 teve o histórico atualizado em 30/10/2026 e está "
                  "vinculado à fatura F9, de R$ 450,00, com vencimento em 10/10/2026.")  # fmt: skip


async def test_protocol_message_text_is_not_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Texto de mensagem pode vir de fora (não confiável): não entra como evidência."""

    async def msgs(*_: Any) -> list[tuple[Any, ...]]:
        return [("Analyst", "Ligue para (11) 91234-5678 hoje.", "livre", SP_NOW)]

    _patch_protocols(monkeypatch)
    monkeypatch.setattr(protocols.q, "messages", msgs)
    out = await protocols._get(ctx(), {"numero": "20260022332"})
    assert not any("91234" in v for r in out.records for v in r.fields.values())


async def test_draft_due_date_covered(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_protocols(monkeypatch)
    out = await protocols._draft(ctx(), {"service_code": "DOC-001"})
    assert out.draft is not None
    assert uncovered(out) == []
