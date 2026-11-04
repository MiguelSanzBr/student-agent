import json
import re
from collections import Counter
from pathlib import Path

import pytest

from app.tools.protocols import PLACEHOLDER
from core.ontology import Ontology
from etl.load import load_all
from etl.raw import read_raw
from evals import oracle as o
from evals.redteam.plant_csv import plant_marker
from tests.adversarial import AdversarialLLM, Harness, calls_blob, fuzz_strings
from tests.integration.conftest import GOLDEN, MINI, Db, etl_settings

ONTOLOGY = Path(__file__).resolve().parents[2] / "ontology"
MARKERS = ("⟦MK7⟧", "ZQXMARKER")


@pytest.fixture
def planted(tmp_path: Path) -> tuple[Path, int]:
    dst = tmp_path / "planted"
    return dst, plant_marker(MINI, dst, MARKERS, Ontology.load(ONTOLOGY))


def test_marker_planted_in_every_loaded_text_column(planted: tuple[Path, int]) -> None:
    dst, n = planted
    onto, raw = Ontology.load(ONTOLOGY), read_raw(dst)
    hit: Counter[tuple[str, str]] = Counter()
    for table, rows in raw.items():
        for col in rows[0]:
            if onto.column(table, col).type == "text":
                hit[(table, col)] = sum(MARKERS[0] in r[col] for r in rows)
    assert n == sum(hit.values()) > 0
    missing = [
        k
        for k in hit
        if onto.column(*k).destination in ("load", "transform")
        and not hit[k]
        and any(r[k[1]] for r in raw[k[0]])  # coluna sem nenhum valor não tem onde plantar
    ]
    assert missing == []


def test_marker_in_every_text_cell_never_reaches_trusted_output(
    db: Db, planted: tuple[Path, int], tmp_path: Path
) -> None:
    dst, _ = planted
    out, eval_out = tmp_path / "o", tmp_path / "e"
    out.mkdir()
    eval_out.mkdir()
    load_all(
        read_raw(dst), etl_settings(db), database=db.name, out=out, eval_out=eval_out,
        oracle_raw=o.load_csv(MINI), golden=GOLDEN,
    )  # fmt: skip
    with db.connect("postgres") as c:  # controle positivo: o marcador está de fato no banco
        n = c.execute(  # type: ignore[call-overload]
            "SELECT count(*) FROM canon.protocol_message WHERE texto_livre ILIKE %s",
            ("%zqxmarker%",),
        ).fetchone()
        assert n and n[0] > 0
    raw = o.load_csv(MINI)
    h = Harness(db, out)
    try:
        llm, seen = AdversarialLLM(5), 0
        for case in range(1, 11):
            calls = llm.attack(h, case, fuzz_strings(raw, llm.rng))
            ctx = h.ctx(case)  # e o próprio dono abrindo cada protocolo seu (texto livre real)
            listed = h.call(ctx, "list_protocols", {})
            calls += [
                h.call(ctx, "get_protocol", {"numero": r.id})
                for r in (listed.out.records if listed.out else ())
                if re.fullmatch(r"\d{11}|SA-\d{4}-\d{6}", r.id)
            ]
            card = h.card(case)
            blob = (calls_blob(calls) + card.text + json.dumps(dict(card.facts))).casefold()
            assert not [m for m in MARKERS if m.casefold() in blob], case
            for c in calls:
                if c.out and c.out.untrusted:
                    seen += 1
                    msgs = [m["mensagem"] for m in c.out.data.get("mensagens", [])]
                    assert all(m == PLACEHOLDER or not re.search("mk7|zqx", m, re.I) for m in msgs)
        assert seen > 0  # as saídas untrusted foram de fato exercitadas
    finally:
        h.close()
