from collections import Counter
from pathlib import Path

import pytest

from evals import oracle as o

pytestmark = pytest.mark.fulldata
ZIP = Path(__file__).resolve().parents[2] / "data" / "dados.zip"
G1, G2, G4 = ("UniSaoPaulo", "181722"), ("UniAtlantico_EAD", "012815"), ("UniSaoPaulo", "180823")


@pytest.fixture(scope="module")
def raw() -> o.Raw:
    if not ZIP.exists():
        pytest.skip("data/dados.zip ausente")
    return o.load_csv(ZIP)


def test_protocol_links_breakdown(raw: o.Raw) -> None:
    links = o.oracle_link_protocols(raw)
    assert Counter(r.rule for r in links.values() if r.rule) == {
        "ra_cpf": 3455,
        "guardian_payer": 73,
        "guardian_payer_unique": 29,
        "cpf_brand_unique": 337,
        "cpf_brand_by_date": 17,
    }
    assert sum(1 for r in links.values() if r.rule) == 3911


def test_unlinked_22(raw: o.Raw) -> None:
    links = o.oracle_link_protocols(raw)
    assert sum(1 for r in links.values() if r.enrollment is None) == 22
    assert all((r.enrollment is None) == (r.rule is None) for r in links.values())


def test_lms_accounts_1003_without_test_accounts(raw: o.Raw) -> None:
    acc = o.oracle_lms_accounts(raw)
    assert len(acc) == 1003 and len(set(acc.values())) == 1003
    names = {int(u["student_lms_id"]): u["username"] for u in raw["lms/mdl_user"]}
    bad = ("aluno.teste", "prof", "admin", "integration")
    assert not [u for u in acc if names[u].startswith(bad)]
    # golden 8: conta manual que loga como pres_18223 -> UniSaoPaulo 182230
    assert acc[101629] == ("UniSaoPaulo", "182230")


def test_snapshot_273_of_273(raw: o.Raw) -> None:
    assert o.oracle_snapshot_check(raw) == (273, 273)


def test_graded_closed_without_attempt_is_0(raw: o.Raw) -> None:
    assert sum(o.oracle_lms(raw, k).encerradas_sem_tentativa for k in o.enrollments(raw)) == 0


def test_open_practice_1217_and_remaining_attempt_1615(raw: o.Raw) -> None:
    facts = [o.oracle_lms(raw, k) for k in o.enrollments(raw)]
    assert sum(f.simulados_abertos for f in facts) == 1217
    assert sum(f.simulados_com_tentativa_restante for f in facts) == 1615


def test_protocol_ref_229(raw: o.Raw) -> None:
    assert o.oracle_coverage(raw)["protocol_ref"] == (229, 229)


def test_coverage_complete(raw: o.Raw) -> None:
    c = o.oracle_coverage(raw)
    assert c["protocolos_ligados"] == 3911 and c["contas_lms"] == c["contas_lms_de_aluno"] == 1003
    assert c["contratos_resolvidos"] == (1065, 1065) and c["ra_dv_valido"] == (1037, 1037)


def test_golden4_finance(raw: o.Raw) -> None:
    f = o.oracle_financial(raw, G4)
    assert o.money(f.total_para_quitar) == "R$ 49.508,43"
    assert o.money(f.principal_vencido) == "R$ 42.636,26"
    assert f.faturas_vencidas == 32 and f.pagadores == ("responsável",)
    assert f.total_para_quitar == f.principal_vencido + f.encargos
    assert sum(f.para_quitar.values()) >= f.total_para_quitar
    assert all(f.para_quitar[i] >= f.saldo[i] for i in f.saldo)


def test_golden1_attempts_7(raw: o.Raw) -> None:
    assert o.oracle_lms(raw, G1).tentativas == 7


def test_golden2_attempts_30(raw: o.Raw) -> None:
    assert o.oracle_lms(raw, G2).tentativas == 30


def test_golden1_protocols(raw: o.Raw) -> None:
    ids = {p["protocolo"] for p in o.oracle_protocols(raw, G1)}
    assert ids == {"20250016398", "20260006621", "20260008724", "20260020465"}


def test_presencial_next_assessment_from_calendar(raw: o.Raw) -> None:
    n = 0
    for k, e in o.enrollments(raw).items():
        if e["MODALITY"] != "CAMPUS" or e["ENROLLMENT_STATUS"] != "ACTIVE":
            continue
        f = o.oracle_lms(raw, k)
        n += 1
        if f.fonte == "lms":
            assert f.proxima_avaliacao and f.proxima_tipo
        else:
            assert (f.proxima_avaliacao, f.fonte) == ("05/12/2026", "calendario")
    assert n > 0
    assert o.oracle_lms(raw, G2).fonte == "lms"  # EAD: pela prova do LMS


def test_catalog_direito_ead_not_offered(raw: o.Raw) -> None:
    for fam in ("UniSaoPaulo", "UniMontanhas", "UniLitoral", "UniAtlantico", "UniAraucaria"):
        assert not o.oracle_catalog(raw, fam, "ead")["DIR"].ofertado
    pre = o.oracle_catalog(raw, "UniSaoPaulo", "presencial")["DIR"]
    assert pre.ofertado and pre.mensalidade and pre.parcelas_por_semestre == 6


def test_ideal_answer_and_self_mutation(raw: o.Raw) -> None:
    import random

    ans, facts = o.ideal_answer({"oracle_fn": "oracle_financial", "args": {"enr": list(G4)}}, raw)
    assert "R$ 49.508,43" in ans and "32" in facts
    assert all(x in ans for x in facts)
    assert o.mutate_one_fact(ans, "money", random.Random(3)) != ans
