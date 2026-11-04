"""Runner da qualidade (analysis_plan.md §1-§15): perfil `eval`, preflight, SHA e rodada
registrados, turno inválido refeito 1x, teto duro aborta, anotação no Phoenix, quality.json/md.

    uv run python -m evals.golden.run --split dev --model gemini-3.8-flash --record --round 1

Roda no container `evals` (docker compose --profile eval): tokens via `evals.tokens.mint`.
`--model` só documenta/precifica: o modelo servido é o `LLM_MODEL` da API.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx

from evals.client import ChatClient, Rejected
from evals.golden.error_cause import attribute_cause
from evals.golden.score import DatasetItem, TurnRecord, is_refusal, score_item
from evals.stats import (
    CI,
    ItemResult,
    Label,
    TurnTrace,
    cohen_kappa,
    corrected_pass_ci,
    p_target,
    ppv_npv,
    two_way_bootstrap_ci,
    wilson,
)

ROOT = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).resolve().parent
EVIDENCE = ROOT / "docs" / "evidence" / "quality"
VALIDATION_SEED = 20261104  # §12
BOOT_SEED = 20261105
CEILING_WAIT_S = 300.0  # Retry-After >= isto = teto de gasto (503 "spend" da admissão): aborta
MAX_WAITS = 120  # esperas consecutivas por Retry-After num turno antes de desistir
PROVIDER_FAIL = frozenset({"error", "llm_unavailable"})
META_TARGET = 0.85
Sleep = Callable[[float], Awaitable[None]]
Annotate = Callable[[str, str, str, float | None, str | None], None]


class _Abort(Exception):
    pass


# ----------------------------------------------------------------------------- rodada
@dataclass(frozen=True)
class Outcome:
    item_id: str
    student: str
    template: str
    category: str
    split: str
    status: str  # pass | fail | invalid
    attempts: int
    reasons: tuple[str, ...] = ()
    cause: str | None = None
    expect_refusal: bool = False
    refused_unduly: bool = False
    turns: tuple[TurnRecord, ...] = ()
    span_id: str | None = None


@dataclass
class RoundResult:
    outcomes: list[Outcome] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    redone: int = 0
    waited_s: float = 0.0

    @property
    def valid(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status != "invalid"]

    @property
    def invalid(self) -> int:
        return len(self.outcomes) - len(self.valid)


async def _turn(
    client: ChatClient, token: str, cid: str, text: str, sleep: Sleep, rr: RoundResult
) -> TurnRecord | None:
    """None = falha do provedor/transporte (429/5xx, timeout). Rejeição antes do stream espera."""
    for _ in range(MAX_WAITS):
        try:
            return await client.ask(token, cid, text)
        except Rejected as e:  # não é tentativa (§5): espera e reenvia
            if (e.retry_after or 0) >= CEILING_WAIT_S:
                raise _Abort(f"teto de gasto (Retry-After {e.retry_after:g}s)") from e
            wait = e.retry_after or 1.0
            rr.waited_s += wait
            await sleep(wait)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 or e.response.status_code >= 500:
                return None
            raise
        except httpx.TransportError:
            return None
    raise _Abort("admissão não liberou o turno")


async def _item(
    item: DatasetItem, token: str, client: ChatClient, sleep: Sleep, rr: RoundResult
) -> Outcome:
    sid = item.id.rsplit("@", 1)[-1]
    base: dict[str, Any] = dict(
        item_id=item.id, student=sid, template=item.template_id, category=item.category,
        split=item.split, expect_refusal=any(c.expect_refusal for c in item.steps),
    )  # fmt: skip
    for attempt in (1, 2):  # refeito 1x, a conversa inteira (id novo); §5
        cid, turns, span, bad = uuid4().hex, [], None, False
        for step in item.steps:
            rec = await _turn(client, token, cid, step.text, sleep, rr)
            span = getattr(client, "last_meta", {}).get("span_id") or span
            if rec is None or rec.cost_degraded or rec.status in PROVIDER_FAIL:
                bad = True
                break
            turns.append(rec)
        if bad:
            rr.redone += attempt == 1
            continue
        sc = score_item(item, turns)
        unduly = any(is_refusal(t.answer) and not c.expect_refusal
                     for c, t in zip(item.steps, turns, strict=True))  # fmt: skip
        cause = None
        if not sc.passed:
            step_i = next((int(r[1 : r.index(":")]) - 1 for r in sc.reasons if r[0] == "t"), 0)
            t = turns[min(step_i, len(turns) - 1)]
            trace = TurnTrace(t.answer, t.tools_called, pre_guard_answer=t.pre_guard_answer,
                              step=step_i)  # fmt: skip
            cause = attribute_cause(item, trace, sc)
        return Outcome(**base, status="pass" if sc.passed else "fail", attempts=attempt,
                       reasons=sc.reasons, cause=cause, refused_unduly=unduly,
                       turns=tuple(turns), span_id=span)  # fmt: skip
    return Outcome(**base, status="invalid", attempts=2, span_id=span)


async def run_round(
    items: Sequence[DatasetItem], tokens: Mapping[str, str], client: ChatClient,
    *, sleep: Sleep = asyncio.sleep, annotate: Annotate | None = None,
) -> RoundResult:  # fmt: skip
    rr = RoundResult()
    for item in items:
        try:
            o = await _item(item, tokens[item.id.rsplit("@", 1)[-1]], client, sleep, rr)
        except _Abort as e:
            rr.aborted, rr.abort_reason = True, str(e)
            break
        rr.outcomes.append(o)
        if annotate and o.span_id and o.status != "invalid":
            annotate(o.span_id, "golden", o.status, float(o.status == "pass"), o.category)
            if o.cause:  # o estágio vira o label da anotação (§11)
                annotate(o.span_id, "causa_erro", o.cause, 0.0, "; ".join(o.reasons)[:500])
    return rr


def append_round(path: Path, rec: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_rounds(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ----------------------------------------------------------------------------- amostras
def _largest_remainder(sizes: Mapping[str, int], k: int) -> dict[str, int]:
    total = sum(sizes.values())
    quota = {c: k * n / total for c, n in sizes.items()}
    out = {c: min(sizes[c], int(q)) for c, q in quota.items()}
    for c in sorted(quota, key=lambda c: (-(quota[c] - int(quota[c])), c)):
        if sum(out.values()) >= k:
            break
        out[c] += out[c] < sizes[c]
    return out


def _stratified(pool: Sequence[ItemResult], k: int, rng: random.Random) -> list[ItemResult]:
    by: dict[str, list[ItemResult]] = defaultdict(list)
    for r in pool:
        by[r.category].append(r)
    alloc = _largest_remainder({c: len(v) for c, v in sorted(by.items())}, k) if pool else {}
    return [x for c, n in alloc.items() for x in rng.sample(by[c], n)]


def sample_scorer_validation(
    results: Sequence[ItemResult], *, seed: int = VALIDATION_SEED
) -> list[ItemResult]:
    """§12: 24 aprovados + 6 reprovados, estratificados por categoria (maior resto)."""
    rng = random.Random(seed)
    ordered = sorted(results, key=lambda r: r.item_id)
    fails = [r for r in ordered if not r.passed]
    n_fail = min(6, len(fails))
    chosen_fail = _stratified(fails, n_fail, rng)
    chosen_pass = _stratified([r for r in ordered if r.passed], 30 - n_fail, rng)
    return sorted(chosen_pass + chosen_fail, key=lambda r: r.item_id)


def sample_utility[T](conversations: Sequence[T], n: int = 30, *, seed: int) -> list[T]:
    return random.Random(seed).sample(list(conversations), min(n, len(conversations)))


# ----------------------------------------------------------------------------- estatística
def _results(outs: Sequence[Outcome]) -> list[ItemResult]:
    return [ItemResult(o.item_id, o.student, o.template, o.status == "pass", o.category)
            for o in outs]  # fmt: skip


def _ci(outs: Sequence[Outcome], strata: Mapping[str, str], n_boot: int) -> dict[str, Any] | None:
    res = _results(outs)
    if not res:
        return None
    try:
        ci = two_way_bootstrap_ci(res, strata=strata, n_boot=n_boot, seed=BOOT_SEED)
    except (IndexError, ValueError):  # subconjunto pequeno demais para reamostrar
        return {"n": len(res), "point": sum(r.passed for r in res) / len(res)}
    return {"n": len(res), "low": ci.low, "high": ci.high, "point": ci.point}


def estimate_icc(results: Sequence[ItemResult]) -> float:
    """ICC (aluno e template) pela variância das médias; shortcut: sem modelo misto, serve só
    para a simulação de P(meta) (§4)."""
    p = sum(r.passed for r in results) / len(results)
    if p in (0.0, 1.0):
        return 0.0

    def comp(key: Callable[[ItemResult], str]) -> float:
        g: dict[str, list[bool]] = defaultdict(list)
        for r in results:
            g[key(r)].append(r.passed)
        means, size = [sum(v) / len(v) for v in g.values()], len(results) / len(g)
        mu = sum(means) / len(means)
        var = sum((m - mu) ** 2 for m in means) / max(1, len(means) - 1)
        return max(0.0, var - p * (1 - p) / size)

    icc = (comp(lambda r: r.student) + comp(lambda r: r.template)) / (p * (1 - p))
    return min(0.95, icc)


def p_meta_table(
    icc: float, p0: float, *, n_students: int, n_templates: int, n_sim: int = 100,
    seed: int = BOOT_SEED,
) -> dict[str, Any]:  # fmt: skip
    """§4: P(limite inferior >= 85%) sob o pass@1 do dev e +/- 5 p.p.; gravada antes da rodada."""
    rows = {}
    for d in (-0.05, 0.0, 0.05):
        p = min(0.999, max(0.001, p0 + d))
        rows[f"{p:.3f}"] = p_target(icc, n_students, n_templates, p, n_sim=n_sim, seed=seed)
    return {"icc": icc, "p_dev": p0, "n_students": n_students, "n_templates": n_templates,
            "n_sim": n_sim, "p_meta": rows}  # fmt: skip


def _bool(x: str) -> bool | None:
    return {"s": True, "sim": True, "1": True, "n": False, "nao": False, "não": False,
            "0": False}.get(x.strip().lower())  # fmt: skip


def read_labels(path: Path, outs: Mapping[str, Outcome]) -> list[Label]:
    if not path.exists():
        return []
    rows = csv.DictReader(path.open(encoding="utf-8", newline=""))
    return [Label(outs[r["item_id"]].status == "pass", ok) for r in rows
            if r["item_id"] in outs and (ok := _bool(r.get("author_correct", ""))) is not None]  # fmt: skip  # noqa: E501


def summarize(
    rr: RoundResult, *, strata: Mapping[str, str], n_boot: int = 10_000,
    labels: Sequence[Label] = (), utility: Sequence[bool] = (),
    judge_verdicts: Mapping[str, bool] | None = None, p_meta: Mapping[str, Any] | None = None,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:  # fmt: skip
    valid = rr.valid
    single = [o for o in valid if o.category != "multiturno"]
    multi = [o for o in valid if o.category == "multiturno"]
    prim = _ci(single, strata, n_boot)
    out: dict[str, Any] = {
        **(meta or {}),
        "aborted": rr.aborted,
        "abort_reason": rr.abort_reason,
        "items": len(rr.outcomes),
        "invalid": rr.invalid,
        "redone": rr.redone,
        "primary": prim,
        "meets_target": bool(prim and prim.get("low", 0) >= META_TARGET),
        "p_meta": p_meta,
        "by_category": {
            c: {"n": len(v), "k": sum(o.status == "pass" for o in v)}
            for c, v in sorted(_group(single, lambda o: o.category).items())
        },
        "by_cause": dict(Counter(o.cause for o in valid if o.cause)),
        "seen_unseen": {
            k: _ci([o for o in single if o.split == s], strata, n_boot)
            for k, s in (("vistos(shared)", "shared"), ("nao_vistos(test_only)", "test_only"))
        },
        "refusal": {
            "correta": _ci([o for o in single if o.expect_refusal], strata, n_boot),
            "indevida_ausente": _ci(
                [
                    replace(o, status="fail" if o.refused_unduly else "pass")
                    for o in single
                    if not o.expect_refusal
                ],
                strata,
                n_boot,
            ),  # fmt: skip
            "cruzamento": _ci([o for o in single if o.category == "cruzamento"], strata, n_boot),
        },
        "multiturn": {"n": len(multi), **_wil(sum(o.status == "pass" for o in multi), len(multi))},
        "sensitivity": None,
        "kappa_juiz": None,
        "utility": None,
    }
    if labels and single:
        tp_ci, tn_ci = ppv_npv(labels)
        cp = corrected_pass_ci(_results(single), labels, strata=strata, n_boot=n_boot,
                               seed=BOOT_SEED)  # fmt: skip
        out["sensitivity"] = {"n_labels": len(labels), "vpp": _d(tp_ci), "vpn": _d(tn_ci),
                              "pass_corrigido": _d(cp)}  # fmt: skip
    if judge_verdicts:
        ids = [o for o in valid if o.item_id in judge_verdicts]
        out["kappa_juiz"] = {"n": len(ids), "kappa": cohen_kappa(
            [o.status == "pass" for o in ids], [judge_verdicts[o.item_id] for o in ids])}  # fmt: skip  # noqa: E501
    if utility:
        out["utility"] = {"n": len(utility), **_wil(sum(utility), len(utility))}
    return out


def _group(outs: Sequence[Outcome], key: Callable[[Outcome], str]) -> dict[str, list[Outcome]]:
    g: dict[str, list[Outcome]] = defaultdict(list)
    for o in outs:
        g[key(o)].append(o)
    return g


def _d(ci: CI) -> dict[str, float]:
    return {"low": ci.low, "high": ci.high, "point": ci.point}


def _wil(k: int, n: int) -> dict[str, float]:
    return _d(wilson(k, n))


def _fmt(ci: Mapping[str, Any] | None) -> str:
    if not ci:
        return "n/d"
    if "low" not in ci:
        return f"{ci['point']:.1%} (n={ci.get('n', '?')}, IC indisponível)"
    return f"{ci['point']:.1%} [{ci['low']:.1%}; {ci['high']:.1%}]" + (
        f" (n={ci['n']})" if "n" in ci else ""
    )


def render_md(s: Mapping[str, Any]) -> str:
    h = [f"# Qualidade: {s['split']} rodada {s['round']}", "",
         f"- SHA `{s['sha']}`{' (árvore suja)' if s.get('dirty') else ''}; modelo `{s['model']}`; "
         f"texto: {'paráfrases (robustez, não primário)' if s.get('paraphrase') else 'base'}",
         f"- itens {s['items']}; inválidos (fora do denominador) {s['invalid']}; refeitos "
         f"{s['redone']}; rodada abortada: {'SIM, ' + s['abort_reason'] if s['aborted'] else 'não'}"]  # fmt: skip  # noqa: E501
    if s.get("test_rounds"):
        h.append("- rodadas do teste (todas contadas, §15): " + "; ".join(
            f"r{r['round']} {r['sha'][:8]}{' abortada' if r.get('aborted') else ''}"
            for r in s["test_rounds"]))  # fmt: skip
    pm = s.get("p_meta")
    h += [
        "",
        "## Primário (pass@1, turno único)",
        f"- {_fmt(s['primary'])}; "
        f"meta (limite inferior >= 85%): {'atingida' if s['meets_target'] else 'não atingida'}",
    ]
    if pm:
        h += [
            "",
            "## P(meta) registrada antes da rodada (§4)",
            f"ICC {pm['icc']:.3f}, "
            f"n={pm['n_students']} alunos x {pm['n_templates']} templates, {pm['n_sim']} sim.",
        ]
        h += [f"- pass@1 verdadeiro {p}: P = {v:.2f}" for p, v in pm["p_meta"].items()]
    h += ["", "## Por categoria (n sempre)"]
    h += [f"- {c}: {v['k']}/{v['n']}" for c, v in s["by_category"].items()]
    h += ["", "## Por causa de erro"] + [f"- {c}: {n}" for c, n in s["by_cause"].items()]
    h += ["", "## Vistos x não vistos"] + [f"- {k}: {_fmt(v)}" for k, v in s["seen_unseen"].items()]
    r = s["refusal"]
    h += ["", "## Recusa e cruzamento", f"- recusa correta: {_fmt(r['correta'])}",
          f"- sem recusa indevida: {_fmt(r['indevida_ausente'])}",
          f"- cruzamento: {_fmt(r['cruzamento'])}",
          "", f"## Multi-turno (secundário): {s['multiturn']['point']:.1%} (n={s['multiturn']['n']})"]  # fmt: skip  # noqa: E501
    se = s["sensitivity"]
    h += ["", "## Sensibilidade (scorer validado)",
          "pendente: rótulos do autor (labels.csv)" if not se else
          f"VPP {_fmt(se['vpp'])}; VPN {_fmt(se['vpn'])}; pass corrigido {_fmt(se['pass_corrigido'])}"]  # fmt: skip  # noqa: E501
    k = s["kappa_juiz"]
    h += [
        "",
        "## Juiz (só kappa)",
        "pendente: sem chamada ao juiz" if not k else f"kappa {k['kappa']:.3f} (n={k['n']})",
    ]
    u = s["utility"]
    h += [
        "",
        "## Utilidade",
        "pendente: utility_labels.csv" if not u else f"{_fmt(u)} (n={u['n']})",
    ]
    if s.get("dev_vs_test"):
        h += ["", "## Dev x teste", s["dev_vs_test"]]
    return "\n".join(h) + "\n"


# ----------------------------------------------------------------------------- dataset
def apply_paraphrases(
    items: Sequence[DatasetItem], shared: Sequence[Mapping[str, Any]]
) -> list[DatasetItem]:
    """Rodada secundária de robustez (§16): paráfrase `int(sha256(item_id)[:8], 16) % n`."""
    para = {t["id"]: t.get("paraphrases") or [] for t in shared}
    out = []
    for it in items:
        ps = para.get(it.template_id) or []
        if ps and len(it.steps) == 1:
            i = int(hashlib.sha256(it.id.encode()).hexdigest()[:8], 16) % len(ps)
            it = replace(it, steps=(replace(it.steps[0], text=ps[i]),))
        out.append(it)
    return out


def pick_enrollment(enrs: Sequence[tuple[str, str]], status: Mapping[tuple[str, str], str],
                    active: bool) -> tuple[str, str]:  # fmt: skip
    """§16: aluno ativo -> menor (marca, RA) entre as ATIVAS; senão, entre todas."""
    pool = [e for e in enrs if status[e] == "ACTIVE"] if active else []
    return min(pool or enrs)


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip


def load_dataset(split: str, *, raw: Any, hmac_key: bytes, holdout_yaml: Path | None
                 ) -> tuple[list[DatasetItem], dict[str, UUID], dict[str, str], list[Any]]:  # fmt: skip  # noqa: E501
    """(itens, aluno -> person_key, aluno -> estrato, templates shared)."""
    from etl.resolve_core import resolve_people
    from evals import oracle as o
    from evals.golden import build

    shared = build.load_shared()
    scripts = build.load_templates(GOLD / "multiturn.yaml")
    templates = list(shared)
    if split == "dev":
        golden = json.loads((ROOT / "tests/fixtures/golden.json").read_text())["cases"]
        login = json.loads((Path(os.environ.get("ETL_OUT_DIR", "/etl-out"))
                            / "golden_login.json").read_text())["cases"]  # fmt: skip
        pk = {f"caso{c['case']}": UUID(str(c["person_key"])) for c in login}
        enr = {f"caso{c['case']}": (c["brand"], c["ra"]) for c in golden}
        strata = dict.fromkeys(enr, "dev")
    else:
        if holdout_yaml is None:
            raise SystemExit("--split test exige --holdout-yaml (templates test_only decifrados)")
        templates += build.load_templates(holdout_yaml)
        people = resolve_people(raw["sag/person"], hmac_key)
        by_pk = {p.person_key: p for p in people.values()}
        status = {e: r["ENROLLMENT_STATUS"] for e, r in o.enrollments(raw).items()}
        pid_enrs: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for e, r in o.enrollments(raw).items():
            pid_enrs[r["PERSON_ID"]].append(e)
        students = build.select_test_students()
        pk, enr, strata = {}, {}, {}
        for s in students:
            p = by_pk[UUID(s.person_key)]
            enrs = [e for pid in p.person_ids for e in pid_enrs[pid]]
            pk[s.person_key], strata[s.person_key] = UUID(s.person_key), f"{s.modalidade}/{s.ativo}"
            enr[s.person_key] = pick_enrollment(enrs, status, s.ativo)
    return build.build_items(templates, scripts, enr, raw), pk, strata, templates


# ----------------------------------------------------------------------------- CLI
def _transcripts(rr: RoundResult, items: Mapping[str, DatasetItem]) -> str:
    rows = []
    for o in rr.valid:
        it = items[o.item_id]
        rows.append({"item_id": o.item_id, "category": o.category, "status": o.status,
                     "turns": [{"q": c.text, "a": t.answer, "pre_guard": t.pre_guard_answer}
                               for c, t in zip(it.steps, o.turns, strict=False)]})  # fmt: skip
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"


def _validation_csv(path: Path, sample: Sequence[ItemResult], rr: RoundResult,
                    items: Mapping[str, DatasetItem]) -> None:  # fmt: skip
    outs = {o.item_id: o for o in rr.outcomes}
    with path.open("w", encoding="utf-8", newline="") as fh:  # sem o veredito: rótulo às cegas
        w = csv.writer(fh)
        w.writerow(["item_id", "pergunta", "resposta", "author_correct"])
        for r in sample:
            o = outs[r.item_id]
            w.writerow([r.item_id, items[r.item_id].steps[-1].text, o.turns[-1].answer, ""])


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=("dev", "test"), required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--record", action="store_true", help="registra a rodada e anota no Phoenix")
    ap.add_argument("--paraphrase", action="store_true", help="rodada secundária de robustez")
    ap.add_argument("--base-url", default=os.environ.get("EVAL_API_URL", "http://api-redteam:8000"))
    ap.add_argument("--data", type=Path, default=ROOT / "data/dados.zip")
    ap.add_argument(
        "--holdout-yaml", type=Path, help="templates test_only decifrados (fora do git)"
    )
    ap.add_argument("--dev-json", type=Path, help="quality.json do dev (ICC para P(meta))")
    ap.add_argument("--labels", type=Path, default=GOLD / "labels.csv")
    ap.add_argument("--utility-labels", type=Path, default=GOLD / "utility_labels.csv")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--phoenix-url", default=os.environ.get("PHOENIX_URL"))
    ap.add_argument("--spend-today", type=Decimal, default=Decimal(0))
    ap.add_argument("--rpd-left", type=int)
    ap.add_argument("--tokens-per-call", type=int, default=3000)
    ap.add_argument("--n-boot", type=int, default=10_000)
    ap.add_argument("--allow-dirty", action="store_true")
    return ap.parse_args(argv)


async def amain(a: argparse.Namespace) -> int:
    from app.config import AppSettings, load_slo_targets
    from evals import oracle as o
    from evals.preflight import project_round
    from evals.tokens import mint

    slo, settings = load_slo_targets(), AppSettings()
    sha, dirty = _git("rev-parse", "HEAD"), bool(_git("status", "--porcelain"))
    if a.split == "test" and dirty and not a.allow_dirty:
        print(
            "teste exige árvore limpa (SHA congelado); use --allow-dirty só em ensaio",
            file=sys.stderr,
        )
        return 2
    rounds_path = EVIDENCE / "rounds.jsonl"
    prior = [r for r in read_rounds(rounds_path) if r["split"] == a.split]
    if a.record and any(r["round"] == a.round for r in prior):
        print(f"rodada {a.round} do {a.split} já registrada", file=sys.stderr)
        return 2
    raw = o.load_csv(a.data)
    items, pks, strata, templates = load_dataset(
        a.split, raw=raw, hmac_key=settings.hmac_key.encode(), holdout_yaml=a.holdout_yaml
    )
    if a.paraphrase:
        items = apply_paraphrases(items, templates)
    turns = sum(len(i.steps) for i in items)
    proj = project_round(
        turns, slo.admission.est_requests, a.tokens_per_call, a.model,
        spend_today=a.spend_today, ceiling=Decimal(str(slo.limits["eval"].global_ceiling_usd)),
        rpd_left=a.rpd_left or slo.quotas[a.model].rpd,
    )  # fmt: skip
    print(f"preflight: ~US$ {proj.cost_usd:.2f}, {proj.calls} chamadas, ok={proj.ok}")
    if not proj.ok:
        return 2
    out = a.out or EVIDENCE / f"{a.split}-r{a.round}"
    out.mkdir(parents=True, exist_ok=True)
    p_meta = None
    if a.split == "test" and a.dev_json:  # gravada ANTES da execução (§4)
        dev = json.loads(a.dev_json.read_text())
        p_meta = p_meta_table(dev["icc"], dev["k"] / dev["n"], n_students=len(pks),
                              n_templates=len({i.template_id for i in items}))  # fmt: skip
        (out / "p_meta.json").write_text(json.dumps(p_meta, indent=2))
    annotate: Annotate | None = None
    if a.record and a.phoenix_url:
        from app.obs import annotations

        annotations.configure(flush=lambda: None, send=annotations.phoenix_sender(a.phoenix_url))
        annotate = annotations.annotate_span
    client = ChatClient(a.base_url, timeout=90.0)
    try:
        rr = await run_round(items, {s: mint(u, "chat") for s, u in pks.items()}, client,
                             annotate=annotate)  # fmt: skip
    finally:
        await client.aclose()
    if annotate:
        await annotations.drain()
    by_id = {i.id: i for i in items}
    meta = {"split": a.split, "round": a.round, "model": a.model, "sha": sha, "dirty": dirty,
            "paraphrase": a.paraphrase, "perfil": "eval"}  # fmt: skip
    outs = {x.item_id: x for x in rr.outcomes}
    util = []
    if a.utility_labels.exists():
        util = [v for r in csv.DictReader(a.utility_labels.open(encoding="utf-8", newline=""))
                if (v := _bool(r.get("util", ""))) is not None]  # fmt: skip
    summary = summarize(rr, strata=strata, n_boot=a.n_boot, labels=read_labels(a.labels, outs),
                        utility=util, p_meta=p_meta, meta=meta)  # fmt: skip
    if a.split == "dev":
        res = _results([x for x in rr.valid if x.category != "multiturno"])
        summary["k"], summary["n"] = sum(r.passed for r in res), len(res)
        summary["icc"] = estimate_icc(res) if res else None
    if a.split == "test":
        summary["test_rounds"] = [*prior, {"round": a.round, "sha": sha, "aborted": rr.aborted}]
        if a.round == 1 and not rr.aborted:
            valid = _results([x for x in rr.valid if x.category != "multiturno"])
            _validation_csv(
                out / "scorer_validation.csv", sample_scorer_validation(valid), rr, by_id
            )
    (out / "quality.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    (out / "quality.md").write_text(render_md(summary), encoding="utf-8")
    (out / "transcripts.jsonl").write_text(_transcripts(rr, by_id), encoding="utf-8")
    if a.record:
        append_round(rounds_path, {"split": a.split, "round": a.round, "sha": sha,
                                   "model": a.model, "aborted": rr.aborted,
                                   "invalid": rr.invalid, "items": len(rr.outcomes)})  # fmt: skip
    print((out / "quality.md").read_text(encoding="utf-8"))
    return 1 if rr.aborted else 0


def main(argv: Sequence[str] | None = None) -> None:
    sys.exit(asyncio.run(amain(parse(argv))))


if __name__ == "__main__":
    main()
