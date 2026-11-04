"""Dataset: templates x alunos -> itens com gabarito do oráculo; seleção dos 40 alunos de teste;
hash de pré-registro e amostra do `oracle_check.csv`.

    uv run python -m evals.golden.build hash          # reescreve dataset.sha256
    uv run python -m evals.golden.build oracle-check  # amostra 20 itens (exige data/dados.zip)
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from evals import oracle as o
from evals.golden.constants import REFUSAL_PREFIX
from evals.golden.score import Check, DatasetItem, TurnRecord, fact_rx

ROOT = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent
SEED = 20261103
Enr = tuple[str, str]
FLAGS = ("in_collection", "tem_acordo", "protocolo_aberto", "lms_manual", "transferido")
QUOTA = 3
N_TEST = 40
HASHED = [
    "evals/golden/templates.yaml", "evals/golden/paraphrases.yaml", "evals/golden/multiturn.yaml",
    "evals/golden/analysis_plan.md", "evals/golden/score.py", "evals/golden/constants.py",
    "evals/golden/build.py", "evals/oracle.py", "evals/holdout/manifest.json",
    "evals/holdout/author.pub",
]  # fmt: skip

__all__ = ["DatasetItem", "EvalStudent", "build_items", "ideal_turns", "select_test_students"]


# ----------------------------------------------------------------------------- templates
def load_templates(path: Path) -> list[dict[str, Any]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(doc["templates"] if isinstance(doc, dict) else doc)


def load_shared() -> list[dict[str, Any]]:
    """Templates `shared` com as paráfrases de `paraphrases.yaml` no campo `paraphrases`."""
    para = yaml.safe_load((GOLD / "paraphrases.yaml").read_text(encoding="utf-8")) or {}
    return [
        {**t, "paraphrases": para.get(t["id"], [])} for t in load_templates(GOLD / "templates.yaml")
    ]


def _facts(fns: Sequence[str], args: Mapping[str, Any], raw: o.Raw) -> tuple[str, list[str]]:
    parts = [o.ideal_answer({"oracle_fn": fn, "args": args}, raw) for fn in fns]
    return " ".join(p[0] for p in parts), [f for p in parts for f in p[1]]


# Zero fatos no gabarito = "não há nada": qualquer valor desse tipo na resposta é inventado.
_EMPTY_FORBIDS = {
    "oracle_financial": r"r\$\s*\d",
    "oracle_catalog": r"r\$\s*\d",
    "oracle_protocols": r"prt-\d",
    "oracle_forecast": r"\b\d{4}/[12]\b",
}
_NOT_ACTIVE = r"(?<!não )(?<!não está )(?<!\w)ativa(?!\w)"


def _auto_forbidden(fns: Sequence[str], args: Mapping[str, Any], enr: Enr, raw: o.Raw) -> list[str]:
    """Padrões de resposta obviamente errada, derivados do oráculo: status trocado (só para quem
    tem 1 matrícula), valores exclusivos de outra matrícula da pessoa e valores onde não há nada."""
    own = o.enrollments(raw)[enr]
    others = [
        k for k, e in o.enrollments(raw).items() if e["PERSON_ID"] == own["PERSON_ID"] and k != enr
    ]
    out: list[str] = []
    for fn in fns:
        mine = o.ideal_answer({"oracle_fn": fn, "args": args}, raw)[1]
        if not mine and fn in _EMPTY_FORBIDS:
            out.append(_EMPTY_FORBIDS[fn])
        for k in others:
            theirs = o.ideal_answer({"oracle_fn": fn, "args": {**args, "enr": list(k)}}, raw)[1]
            out += [fact_rx(f) for f in theirs if f not in mine and re.search(r"\d.*[,/]|R\$", f)]
        if fn == "oracle_courses" and not others:
            status = own["ENROLLMENT_STATUS"]
            wrong = [w for st, w in o.ENROLLMENT_STATUS_PT.items() if st != status]
            out += [fact_rx(w) for w in wrong]
            if status == "ACTIVE":
                out.append(fact_rx("inativa"))
            else:
                out.append(_NOT_ACTIVE)
    return out


def _check(t: Mapping[str, Any], enr: Enr, raw: o.Raw) -> Check:
    fns = t.get("oracle_fn") or ()
    fns = (fns,) if isinstance(fns, str) else tuple(fns)
    args = {**t.get("args", {}), "enr": list(enr)}
    oracle = t.get("required") == "oracle"
    required = tuple(_facts(fns, args, raw)[1]) if oracle else ()
    forbidden = (*t.get("forbidden", ()), *(_auto_forbidden(fns, args, enr, raw) if oracle else ()))
    return Check(
        text=t["text"], expected_tools=tuple(t.get("expected_tools", ())), required=required,
        forbidden=tuple(dict.fromkeys(forbidden)), expect_draft=bool(t.get("expect_draft")),
        expect_refusal=bool(t.get("expect_refusal")), oracle_fns=fns, args=args,
    )  # fmt: skip


def _applies(t: Mapping[str, Any], enr: Enr, raw: o.Raw) -> bool:
    want = t.get("student")
    if want is None:
        return True
    return (o.oracle_courses(raw, enr).status == "ACTIVE") == (want == "ativo")


def build_items(
    shared: Sequence[Mapping[str, Any]], scripts: Sequence[Mapping[str, Any]],
    enr_by_student: Mapping[str, Enr], raw: o.Raw,
) -> list[DatasetItem]:  # fmt: skip
    """Um item por (template, aluno); um item por (roteiro multi-turno, aluno)."""
    items = []
    for sid, enr in enr_by_student.items():
        for t in shared:
            if _applies(t, enr, raw):
                items.append(DatasetItem(f"{t['id']}@{sid}", t["id"], t["category"], t["split"],
                                         (_check(t, enr, raw),)))  # fmt: skip
        for s in scripts:
            if not _applies(s, enr, raw):
                continue
            steps = tuple(_check(turn, enr, raw) for turn in s["turns"])
            items.append(DatasetItem(f"{s['id']}@{sid}", s["id"], "multiturno", "shared", steps))
    return items


def ideal_turns(item: DatasetItem, raw: o.Raw) -> list[TurnRecord]:
    """Resposta ideal de cada passo, embrulhada em TurnRecord (usa o oráculo, não o scorer)."""
    out = []
    for c in item.steps:
        if c.expect_refusal:
            out.append(
                TurnRecord(f"{REFUSAL_PREFIX} Posso ajudar com notas, finanças ou protocolos.")
            )
            continue
        text = _facts(c.oracle_fns, c.args, raw)[0] if c.oracle_fns else ""
        if c.expect_draft:
            text = (text + " Preparei o rascunho do pedido. Confira e confirme.").strip()
        out.append(TurnRecord(text or "Não tenho essa informação. Pode detalhar o que precisa?",
                              c.expected_tools, c.expect_draft))  # fmt: skip
    return out


def mutate_turn(t: TurnRecord, rng: random.Random) -> TurnRecord:
    """Troca 1 fato (dinheiro > data > nota > número); ValueError se não houver nenhum."""
    for kind in ("money", "date", "grade", "number"):
        try:
            return replace(t, answer=o.mutate_one_fact(t.answer, kind, rng))
        except ValueError:
            continue
    raise ValueError("sem fato mutável")


# ----------------------------------------------------------------------------- alunos de teste
@dataclass(frozen=True)
class EvalStudent:
    person_key: str
    modalidade: str
    ativo: bool
    n_matriculas: int
    in_collection: bool
    tem_acordo: bool
    protocolo_aberto: bool
    lms_manual: bool
    transferido: bool
    payer_type: str | None

    @property
    def stratum(self) -> tuple[str, bool]:
        return (self.modalidade, self.ativo)


def _quota_hits(s: EvalStudent) -> list[str]:
    hits = [f for f in FLAGS if getattr(s, f)]
    if s.n_matriculas >= 2:
        hits.append("2_matriculas")
    if s.payer_type in ("responsavel", "empresa"):
        hits.append(f"pagador_{s.payer_type}")
    return hits


def _load_students(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or Path(os.environ.get("EVAL_OUT_DIR", "/eval-out")) / "eval_students.json"
    return list(json.loads(p.read_text(encoding="utf-8")))


def _load_golden_keys(path: Path | None = None) -> set[str]:
    """person_keys dos golden (`golden_login.json` do etl-out); falha alto se não existir."""
    p = path or Path(os.environ.get("ETL_OUT_DIR", "/etl-out")) / "golden_login.json"
    return {str(c["person_key"]) for c in json.loads(p.read_text(encoding="utf-8"))["cases"]}


def select_test_students(
    seed: int = SEED, *, students: Sequence[Mapping[str, Any]] | None = None,
    golden_keys: set[str] | None = None,
) -> list[EvalStudent]:  # fmt: skip
    """40 alunos: cotas >= 3 em cada traço raro, o resto distribuído pelos estratos
    modalidade x {ativo, inativo} (sempre o estrato menos representado).
    Sem os golden (`golden_keys`; por padrão lê o golden_login.json de ETL_OUT_DIR)."""
    rows = students if students is not None else _load_students()
    gold = golden_keys if golden_keys is not None else _load_golden_keys()
    pool = [EvalStudent(**{k: r[k] for k in EvalStudent.__dataclass_fields__})
            for r in rows if r["person_key"] not in gold]  # fmt: skip
    pool.sort(key=lambda s: s.person_key)
    random.Random(seed).shuffle(pool)
    sel: list[EvalStudent] = []
    need = {h: QUOTA for s in pool for h in _quota_hits(s)}
    for h in list(need):
        while sum(h in _quota_hits(s) for s in sel) < QUOTA:
            nxt = next((s for s in pool if s not in sel and h in _quota_hits(s)), None)
            if nxt is None:
                raise ValueError(f"cota impossível: {h}")
            sel.append(nxt)
    missing = {"2_matriculas", "pagador_responsavel", "pagador_empresa", *FLAGS} - set(need)
    if missing or len(sel) > N_TEST:
        raise ValueError(f"cotas sem candidatos ou estouradas: {sorted(missing)}")
    by_stratum = Counter(s.stratum for s in sel)
    strata = sorted({s.stratum for s in pool})
    while len(sel) < N_TEST:
        for st in sorted(strata, key=lambda x: (by_stratum[x], x)):
            nxt = next((s for s in pool if s.stratum == st and s not in sel), None)
            if nxt:
                sel.append(nxt)
                by_stratum[st] += 1
                break
        else:
            raise ValueError("alunos insuficientes")
    return sorted(sel, key=lambda s: s.person_key)


# ----------------------------------------------------------------------------- pré-registro
def write_dataset_hash() -> None:
    files = HASHED + sorted(
        f"evals/holdout/{p.name}" for p in (ROOT / "evals/holdout").glob("*.age")
    )
    lines = [f"{hashlib.sha256((ROOT / f).read_bytes()).hexdigest()}  {f}" for f in files]
    (GOLD / "dataset.sha256").write_text("\n".join(lines) + "\n")


def write_oracle_check(source: Path, golden: Path, n: int = 20) -> None:
    """Amostra n itens `shared` com fatos (dos 10 golden) para o autor conferir à mão."""
    raw = o.load_csv(source)
    cases = json.loads(golden.read_text())["cases"]
    enr = {f"caso{c['case']}": (c["brand"], c["ra"]) for c in cases}
    items = [i for i in build_items(load_shared(), [], enr, raw) if i.steps[0].required]
    rows = []
    for it in random.Random(SEED).sample(items, n):
        (t,) = ideal_turns(it, raw)
        rows.append({"item_id": it.id, "matricula": "/".join(it.steps[0].args["enr"]),
                     "pergunta": it.steps[0].text, "resposta_ideal": t.answer,
                     "fatos": " | ".join(it.steps[0].required), "conferido": "",
                     "observacao": ""})  # fmt: skip
    with (GOLD / "oracle_check.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        write_dataset_hash()
    elif sys.argv[1:] == ["oracle-check"]:
        write_oracle_check(ROOT / "data/dados.zip", ROOT / "tests/fixtures/golden.json")
    else:
        sys.exit(__doc__)
