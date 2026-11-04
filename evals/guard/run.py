"""Guard x scorer (spec §6.4, "Medição"): perturbação cega 1x (IC agrupado por template de
mutação), rascunhos do dev perturbados, cruzamento guard x scorer e bloqueio indevido com IC.

    uv run python -m evals.guard.run --blind-seed N [--dev-transcripts transcripts.jsonl]

Escreve `guard.json` + `guard.md`. O conjunto cego (`perturb_blind`) é medido UMA vez: o
runner grava a semente e se recusa a repetir a medição se o `guard.json` já existe.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.guards.evidence import check_evidence
from app.tools.base import EntityRecord, ToolCallTrace, index_records
from core.ontology import Ontology
from evals import oracle as o
from evals.golden.score import TurnRecord, score_item
from evals.guard.perturb_blind import build_blind_set
from evals.guard.perturb_dev import _FACT_KEYS, PREFIX
from evals.stats import CI, wilson

ROOT = Path(__file__).resolve().parents[2]
ZERO_CARD = {"1.total_para_quitar": "R$ 0,00", "1.faturas_vencidas": "0"}
EMPTY_TOOLS = {"oracle_protocols": "list_protocols"}


def _percentile(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


def cluster_ci(groups: Mapping[str, Sequence[bool]], *, n_boot: int, seed: int) -> CI:
    """Recall agrupado por template de mutação: reamostra os templates e junta os itens."""
    keys, rng = sorted(groups), random.Random(seed)
    tot = sum(map(len, groups.values()))
    point = sum(map(sum, groups.values())) / tot
    reps = []
    for _ in range(n_boot):
        pick = rng.choices(keys, k=len(keys))
        n = sum(len(groups[k]) for k in pick)
        reps.append(sum(sum(groups[k]) for k in pick) / n)
    return CI(_percentile(reps, 0.025), _percentile(reps, 0.975), point)


def card_for(fn: str, enr: Sequence[str], raw: o.Raw) -> tuple[
    tuple[EntityRecord, ...], dict[str, str], tuple[ToolCallTrace, ...]
]:  # fmt: skip
    """Registros e cartão que o agente teria para esse fato (mesma regra do dev)."""
    _, facts = o.ideal_answer({"oracle_fn": fn, "args": {"enr": list(enr)}}, raw)
    if not facts:
        tool = EMPTY_TOOLS.get(fn)
        return (), dict(ZERO_CARD), (ToolCallTrace(tool, {}, 0),) if tool else ()
    if fn == "oracle_courses":
        facts = [facts[0], facts[1] + "%"]
    card = {f"1.{k}": v for k, v in zip(_FACT_KEYS[fn], facts, strict=False)}
    recs = ()
    if fn == "oracle_protocols":
        recs = tuple(EntityRecord("protocolo", p, (p,), {"status": "aberto"}) for p in facts[1:])
    return recs, card, ()


def blocked(answer: str, fn: str, enr: Sequence[str], raw: o.Raw, onto: Ontology) -> bool:
    recs, card, calls = card_for(fn, enr, raw)
    res = check_evidence(
        answer, question="", records_by_entity=index_records(recs), card_facts=card,
        enrollment_indices=("1",), tool_calls_this_turn=calls, system_notes=(),
        canary="c" * 32, prompt_prefix=PREFIX, ontology=onto,
    )  # fmt: skip
    return not res.ok


def _w(k: int, n: int) -> dict[str, float | int]:
    c = wilson(k, n)
    return {"k": k, "n": n, "point": c.point, "low": c.low, "high": c.high}


def blind_report(raw: o.Raw, onto: Ontology, *, seed: int, n_boot: int) -> dict[str, Any]:
    items = build_blind_set(raw, seed=seed)
    by_t: dict[str, list[bool]] = defaultdict(list)
    ctl = []
    for it in items:
        b = blocked(it.mutated, it.oracle_fn, it.args["enr"], raw, onto)
        (by_t[it.template] if it.expect_block else ctl).append(b)
    by_t.pop("control", None)
    caught = sum(map(sum, by_t.values()))
    return {
        "seed": seed, "n_mutated": sum(map(len, by_t.values())), "n_controls": len(ctl),
        "recall": _d(cluster_ci(by_t, n_boot=n_boot, seed=seed)),
        "recall_by_template": {t: _w(sum(v), len(v)) for t, v in sorted(by_t.items())},
        "false_block": _w(sum(ctl), len(ctl)), "caught": caught,
    }  # fmt: skip


def _d(c: CI) -> dict[str, float]:
    return {"low": c.low, "high": c.high, "point": c.point}


def dev_report(
    transcripts: Sequence[Mapping[str, Any]], items: Mapping[str, Any], raw: o.Raw,
    onto: Ontology, *, seed: int,
) -> dict[str, Any]:  # fmt: skip
    """Rascunhos do dev (pré-guard): originais -> bloqueio indevido; perturbados -> guard x scorer."""  # noqa: E501
    from evals.golden.build import mutate_turn

    rng = random.Random(seed)
    cross = {"guard_bloqueia_scorer_reprova": 0, "guard_bloqueia_scorer_passa": 0,
             "guard_passa_scorer_reprova": 0, "guard_passa_scorer_passa": 0}  # fmt: skip
    ok_n = ok_blocked = skipped = 0
    for row in transcripts:
        it = items.get(row["item_id"])
        if it is None or len(it.steps) != 1 or len(it.steps[0].oracle_fns) != 1:
            skipped += 1
            continue
        c, fn = it.steps[0], it.steps[0].oracle_fns[0]
        if fn not in _FACT_KEYS or not row["turns"]:
            skipped += 1
            continue
        t = row["turns"][0]
        draft = TurnRecord(t["pre_guard"] or t["a"], c.expected_tools, c.expect_draft)
        enr = c.args["enr"]
        if score_item(it, [draft]).passed:  # rascunho certo: o guard não pode bloqueá-lo
            ok_n += 1
            ok_blocked += blocked(draft.answer, fn, enr, raw, onto)
        try:
            pert = mutate_turn(draft, rng)
        except ValueError:
            continue
        g = blocked(pert.answer, fn, enr, raw, onto)
        s = not score_item(it, [pert]).passed
        cross[f"guard_{'bloqueia' if g else 'passa'}_scorer_{'reprova' if s else 'passa'}"] += 1
    n_pert = sum(cross.values())
    return {"skipped": skipped, "rascunhos_certos": _w(ok_blocked, ok_n),
            "perturbados": n_pert, "cruzamento": cross}  # fmt: skip


def render_md(g: Mapping[str, Any]) -> str:
    b = g["blind"]
    f = lambda w: f"{w['point']:.1%} [{w['low']:.1%}; {w['high']:.1%}]"  # noqa: E731
    md = ["# Guard: perturbação cega e guard x scorer", "",
          f"- semente do conjunto cego: {b['seed']} (medido 1x); {b['n_mutated']} mutados, "
          f"{b['n_controls']} controles",
          f"- recall (IC agrupado por template de mutação): {f(b['recall'])}",
          f"- bloqueio indevido nos controles: {b['false_block']['k']}/{b['false_block']['n']} "
          f"{f(b['false_block'])}", "", "## Recall por template (Wilson)"]  # fmt: skip
    md += [f"- {t}: {w['k']}/{w['n']} {f(w)}" for t, w in b["recall_by_template"].items()]
    d = g.get("dev")
    md += ["", "## Rascunhos do dev x scorer"]
    if d:
        w = d["rascunhos_certos"]
        md += [f"- bloqueio indevido em rascunho que o scorer aprova: {w['k']}/{w['n']} {f(w)}",
               f"- perturbados: {d['perturbados']} (sem cartão/oráculo único: {d['skipped']})"]  # fmt: skip  # noqa: E501
        md += [f"- {k}: {v}" for k, v in d["cruzamento"].items()]
    else:
        md.append("pendente: transcripts.jsonl da rodada do dev")
    return "\n".join(md) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--blind-seed", type=int, required=True)
    ap.add_argument("--data", type=Path, default=ROOT / "data/dados.zip")
    ap.add_argument("--dev-transcripts", type=Path)
    ap.add_argument("--out", type=Path, default=ROOT / "docs/evidence/guard")
    ap.add_argument("--n-boot", type=int, default=10_000)
    a = ap.parse_args(argv)
    if (a.out / "guard.json").exists():
        print("conjunto cego já medido (guard.json existe): 1x só", file=sys.stderr)
        return 2
    raw, onto = o.load_csv(a.data), Ontology.load(ROOT / "ontology")
    g: dict[str, Any] = {"blind": blind_report(raw, onto, seed=a.blind_seed, n_boot=a.n_boot)}
    if a.dev_transcripts:
        from evals.golden.build import build_items, load_shared

        golden = json.loads((ROOT / "tests/fixtures/golden.json").read_text())["cases"]
        enr = {f"caso{c['case']}": (c["brand"], c["ra"]) for c in golden}
        items = {i.id: i for i in build_items(load_shared(), [], enr, raw)}
        rows = [json.loads(x) for x in a.dev_transcripts.read_text().splitlines()]
        g["dev"] = dev_report(rows, items, raw, onto, seed=a.blind_seed)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "guard.json").write_text(json.dumps(g, indent=2, ensure_ascii=False))
    (a.out / "guard.md").write_text(render_md(g), encoding="utf-8")
    print(render_md(g))
    return 0


if __name__ == "__main__":
    sys.exit(main())
