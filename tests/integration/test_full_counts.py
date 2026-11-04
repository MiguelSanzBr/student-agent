import json
import os
import re
from pathlib import Path

import pytest

from etl.raw import read_raw
from tests.integration.conftest import Db, etl_settings

pytestmark = pytest.mark.fulldata
ZIP = Path(__file__).resolve().parents[2] / "data" / "dados.zip"


def test_full_counts(db: Db, tmp_path: Path) -> None:
    if not ZIP.exists():
        pytest.skip("data/dados.zip ausente")
    from etl.load import load_all
    from evals import oracle

    out, eval_out = tmp_path / "o", tmp_path / "e"
    out.mkdir(), eval_out.mkdir()
    golden = Path(__file__).resolve().parents[1] / "fixtures" / "golden.json"
    r = load_all(
        read_raw(ZIP), etl_settings(db), database=db.name, out=out, eval_out=eval_out,
        oracle_raw=oracle.load_csv(ZIP), golden=golden,
    )  # fmt: skip
    c = r.sql_counts
    assert (c["person"], c["enrollment"], c["contract"]) == (1000, 1037, 1065)
    assert c["protocol"] == 3911 and r.protocols_unlinked == 22
    assert r.protocols_by_rule == {
        "ra_cpf": 3455, "guardian_payer": 73, "guardian_payer_unique": 29,
        "cpf_brand_unique": 337, "cpf_brand_by_date": 17,
    }  # fmt: skip
    assert c["lms_account"] == 1003
    assert r.protocol_ref == (229, 229) and r.snapshot == (273, 273)
    lms = r.lms
    assert lms["encerradas_sem_tentativa"] == 0
    assert (lms["simulados_abertos"], lms["simulados_com_tentativa_restante"]) == (1217, 1615)
    assert r.logins_absent == 29 and r.logins_present == 0
    assert all(k.ok for k in r.checks)
    js = json.loads((out / "data-quality.json").read_text())
    assert js["sql_counts"] == js["memory_counts"]
    assert not re.search(r"\b\d{11}\b", (out / "data-quality.md").read_text())
    assert os.path.getsize(eval_out / "eval_students.json") > 0
