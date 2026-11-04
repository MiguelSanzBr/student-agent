"""Runner da qualidade (analysis_plan.md §1-§15): perfil `eval`, preflight, SHA e rodada
registrados, turno inválido refeito 1x, teto duro aborta, anotação no Phoenix, quality.json/md.

Roda no container `evals` (ver `make eval-dev|eval-test`; ops/compose.eval.yml): tokens por
`evals.tokens.mint` (segredos em /run/secrets/app), dados em /data, ETL em /etl-out e /eval-out.

    python -m evals.golden.run --split dev --model gemini-3.8-flash --record --round 1
    python -m evals.golden.run --resummarize DIR [--labels L --utility U --judge J --dev-dir D]

`--model` precisa casar com o modelo que a API serve (`done.model` do 1º turno; senão aborta).
No teste não há graus de liberdade: --record, --dev-json, --spend-today e --rpd-left exigidos,
n_boot fixo, sem --allow-dirty/--paraphrase (só com --rehearsal, que não é contado). As saídas
com texto de pergunta (transcripts, scorer_validation) ficam fora do repositório.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import os
import random
import shutil
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
N_BOOT = 10_000  # §2
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
    served_models: dict[str, int] = field(default_factory=dict)  # `done.model` por turno

    @property
    def valid(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.status != "invalid"]

    @property
    def invalid(self) -> int:
        return len(self.outcomes) - len(self.valid)


async def _turn(
    client: ChatClient, token: str, cid: str, text: str, sleep: Sleep, rr: RoundResult
) -> TurnRecord | None:
    """None = falha do provedor/transporte (5xx, timeout; o 429 do provedor chega como evento
    `error` do stream). 429/503 de admissão antes do stream = `Rejected`: espera."""
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
            if e.response.status_code >= 500:
                return None
            raise
        except httpx.TransportError:
            return None
    raise _Abort("admissão não liberou o turno")


async def _item(
    item: DatasetItem, token: str, client: ChatClient, sleep: Sleep, rr: RoundResult,
    expect_model: str | None,
) -> Outcome:  # fmt: skip
    sid = item.id.rsplit("@", 1)[-1]
    base: dict[str, Any] = dict(
        item_id=item.id, student=sid, template=item.template_id, category=item.category,
        split=item.split, expect_refusal=any(c.expect_refusal for c in item.steps),
    )  # fmt: skip
    for attempt in (1, 2):  # refeito 1x, a conversa inteira (id novo); §5
        cid, turns, metas, span, bad = uuid4().hex, [], [], None, False
        for step in item.steps:
            rec = await _turn(client, token, cid, step.text, sleep, rr)
            meta = dict(getattr(client, "last_meta", {}))
            span = meta.get("span_id") or span
            if rec is None or rec.cost_degraded or rec.status in PROVIDER_FAIL:
                bad = True
                break
            served = str(meta.get("model") or "")
            if served:
                rr.served_models[served] = rr.served_models.get(served, 0) + 1
            if expect_model and not rr.outcomes and not turns and served != expect_model:
                raise _Abort(
                    f"modelo servido {served!r} != --model {expect_model!r}" if served
                    else "modelo servido ausente (done.model): não dá para conferir --model"
                )  # fmt: skip
            turns.append(rec)
            metas.append(meta)
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
            tv = metas[min(step_i, len(metas) - 1)].get("tool_values")
            trace = TurnTrace(t.answer, t.tools_called, pre_guard_answer=t.pre_guard_answer,
                              step=step_i, tool_values=tv)  # fmt: skip
            cause = attribute_cause(item, trace, sc)
        return Outcome(**base, status="pass" if sc.passed else "fail", attempts=attempt,
                       reasons=sc.reasons, cause=cause, refused_unduly=unduly,
                       turns=tuple(turns), span_id=span)  # fmt: skip
    return Outcome(**base, status="invalid", attempts=2, span_id=span)


async def run_round(
    items: Sequence[DatasetItem], tokens: Mapping[str, str] | Callable[[str], str],
    client: ChatClient, *, sleep: Sleep = asyncio.sleep, annotate: Annotate | None = None,
    expect_model: str | None = None,
) -> RoundResult:  # fmt: skip
    """`tokens`: aluno -> token (mapa ou função; chamada só quando o aluno aparece).
    Qualquer exceção inesperada aborta a rodada, mantendo o que já foi medido."""
    rr, cache = RoundResult(), {}
    for item in items:
        sid = item.id.rsplit("@", 1)[-1]
        try:
            if sid not in cache:
                cache[sid] = tokens(sid) if callable(tokens) else tokens[sid]
            o = await _item(item, cache[sid], client, sleep, rr, expect_model)
        except _Abort as e:
            rr.aborted, rr.abort_reason = True, str(e)
            break
        except Exception as e:  # noqa: BLE001 - a rodada é contada: aborta e declara
            rr.aborted, rr.abort_reason = True, f"exceção {type(e).__name__}: {str(e)[:200]}"
            break
        rr.outcomes.append(o)
        if annotate and o.span_id and o.status != "invalid":
            annotate(o.span_id, "golden", o.status, float(o.status == "pass"), o.category)
            if o.cause:  # o estágio vira o label da anotação (§11)
                annotate(o.span_id, "causa_erro", o.cause, 0.0, "; ".join(o.reasons)[:500])
    return rr


class RoundExists(Exception):
    pass


def read_rounds(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(path: Path, rec: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def claim_round(path: Path, rec: Mapping[str, Any]) -> None:
    """Linha `started` ANTES de rodar: uma rodada que morre no meio continua contada."""
    if any(r["split"] == rec["split"] and r["round"] == rec["round"] for r in read_rounds(path)):
        raise RoundExists(f"rodada {rec['round']} do {rec['split']} já registrada")
    _append(path, {**rec, "status": "started"})


def close_round(path: Path, rec: Mapping[str, Any]) -> None:
    _append(path, {**rec, "status": "aborted" if rec.get("aborted") else "done"})


class StateError(Exception):
    pass


def _git(*a: str) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()  # fmt: skip


def code_state(env: Mapping[str, str] = os.environ, *, use_git: bool = True) -> tuple[str, bool]:
    """(SHA, árvore suja). No container não há git: o Makefile passa GIT_SHA e GIT_DIRTY."""
    if env.get("GIT_SHA"):
        return env["GIT_SHA"], env.get("GIT_DIRTY", "") not in ("", "0")
    if use_git:
        try:
            return _git("rev-parse", "HEAD"), bool(_git("status", "--porcelain"))
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    raise StateError("GIT_SHA ausente (e sem git): rode pelo make, que passa GIT_SHA/GIT_DIRTY")


def frozen_sha() -> str | None:
    """SHA congelado: env FROZEN_SHA, arquivo `evals/golden/FROZEN_SHA` ou tag `eval-frozen`."""
    if os.environ.get("FROZEN_SHA"):
        return os.environ["FROZEN_SHA"].strip()
    f = GOLD / "FROZEN_SHA"
    if f.exists():
        return f.read_text().strip() or None
    try:
        return _git("rev-list", "-n", "1", "eval-frozen")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def round_states(rows: Sequence[Mapping[str, Any]], split: str) -> list[dict[str, Any]]:
    """Uma entrada por rodada; só `started` (processo morto) conta como abortada."""
    last: dict[int, Mapping[str, Any]] = {}
    for r in rows:
        if r["split"] == split and (r["round"] not in last or r["status"] != "started"):
            last[r["round"]] = r
    return [
        {"round": n, "sha": r["sha"], "aborted": r["status"] == "started" or bool(r.get("aborted"))}
        for n, r in sorted(last.items())
    ]


def is_primary(frozen: str | None, sha: str, *, prior: Sequence[Mapping[str, Any]]) -> bool:
    """Primário = 1ª rodada do teste, no SHA congelado (§1, §15)."""
    return frozen is not None and sha == frozen and not prior


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


def _ci(
    outs: Sequence[Outcome], strata: Mapping[str, str], n_boot: int, *, strict: bool = False
) -> dict[str, Any] | None:
    """`strict` (primário): falha alto. Subconjuntos pequenos caem para o ponto sem IC."""
    res = _results(outs)
    if not res:
        if strict:
            raise ValueError("primário sem itens válidos")
        return None
    try:
        ci = two_way_bootstrap_ci(res, strata=strata, n_boot=n_boot, seed=BOOT_SEED)
    except (IndexError, ValueError, KeyError):  # subconjunto pequeno demais para reamostrar
        if strict:
            raise
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
    icc: float, p0: float, *, n_students: int, n_templates: int, n_sim: int = 200,
    seed: int = BOOT_SEED,
) -> dict[str, Any]:  # fmt: skip
    """§4: P(limite inferior >= 85%) sob o pass@1 do dev e +/- 5 p.p.; gravada antes da rodada."""
    rows = {}
    for d in (-0.05, 0.0, 0.05):
        p = min(0.999, max(0.001, p0 + d))
        rows[f"{p:.3f}"] = p_target(icc, n_students, n_templates, p, n_sim=n_sim, seed=seed)
    se = {k: math.sqrt(v * (1 - v) / n_sim) for k, v in rows.items()}  # erro de Monte Carlo
    return {"icc": icc, "p_dev": p0, "n_students": n_students, "n_templates": n_templates,
            "n_sim": n_sim, "p_meta": rows, "se": se}  # fmt: skip


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
    prim = _ci(single, strata, n_boot, strict=True)
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
        h += [
            f"- pass@1 verdadeiro {p}: P = {v:.2f} (EP Monte Carlo {pm['se'][p]:.3f})"
            for p, v in pm["p_meta"].items()
        ]
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


# ----------------------------------------------------------------------------- saídas
def write_private(
    out: Path, rr: RoundResult, items: Mapping[str, DatasetItem], strata: Mapping[str, str]
) -> None:
    """`transcripts.jsonl`: texto das perguntas (pode ser `test_only`): NUNCA no git. Carrega o
    bastante para re-sumarizar offline (`--resummarize`)."""
    rows = []
    for o in rr.outcomes:
        it = items[o.item_id]
        rows.append({
            "item_id": o.item_id, "student": o.student, "stratum": strata.get(o.student, ""),
            "template": o.template, "category": o.category, "split": o.split, "status": o.status,
            "attempts": o.attempts, "reasons": list(o.reasons), "cause": o.cause,
            "expect_refusal": o.expect_refusal, "refused_unduly": o.refused_unduly,
            "turns": [{"q": c.text, "a": t.answer, "pre_guard": t.pre_guard_answer}
                      for c, t in zip(it.steps, o.turns, strict=False)],
        })  # fmt: skip
    (out / "transcripts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def _validation_csv(path: Path, sample: Sequence[ItemResult], rr: RoundResult,
                    items: Mapping[str, DatasetItem]) -> None:  # fmt: skip
    outs = {o.item_id: o for o in rr.outcomes}
    with path.open("w", encoding="utf-8", newline="") as fh:  # sem o veredito: rótulo às cegas
        w = csv.writer(fh)
        w.writerow(["item_id", "pergunta", "resposta", "author_correct"])
        for r in sample:
            o = outs[r.item_id]
            w.writerow([r.item_id, items[r.item_id].steps[-1].text, o.turns[-1].answer, ""])


def publish(out: Path, evidence: Path | None, tag: str) -> None:
    """Só o que não tem texto de pergunta vai para docs/evidence (quality.json/md, p_meta.json)."""
    if evidence is None or not evidence.parent.exists():
        return
    dest = evidence / tag
    dest.mkdir(parents=True, exist_ok=True)
    for n in ("quality.json", "quality.md", "p_meta.json"):
        if (out / n).exists():
            shutil.copyfile(out / n, dest / n)


def _read_util(path: Path | None) -> list[bool]:
    if path is None or not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [v for r in csv.DictReader(fh) if (v := _bool(r.get("util", ""))) is not None]


def _read_judge(path: Path | None) -> dict[str, bool] | None:
    if path is None or not path.exists():
        return None
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["item_id"]: bool(r["verdict"]) for r in rows}


def build_summary(
    rr: RoundResult, strata: Mapping[str, str], meta: Mapping[str, Any], *, n_boot: int,
    labels: Path | None, utility: Path | None, judge: Path | None, dev_dir: Path | None,
    p_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:  # fmt: skip
    outs = {x.item_id: x for x in rr.outcomes}
    s = summarize(
        rr, strata=strata, n_boot=n_boot, labels=read_labels(labels, outs) if labels else [],
        utility=_read_util(utility), judge_verdicts=_read_judge(judge), p_meta=p_meta, meta=meta,
    )  # fmt: skip
    if meta.get("split") == "dev":
        res = _results([x for x in rr.valid if x.category != "multiturno"])
        s["k"], s["n"] = sum(r.passed for r in res), len(res)
        s["icc"] = estimate_icc(res) if res else None
    if dev_dir is not None:
        dev = json.loads((dev_dir / "quality.json").read_text(encoding="utf-8"))
        s["dev_vs_test"] = f"dev {_fmt(dev['primary'])} x teste {_fmt(s['primary'])}"
    return s


def write_summary(out: Path, s: Mapping[str, Any]) -> None:
    (out / "quality.json").write_text(json.dumps(s, indent=2, ensure_ascii=False))
    (out / "quality.md").write_text(render_md(s), encoding="utf-8")


_META_KEYS = ("split", "round", "model", "sha", "dirty", "paraphrase", "perfil", "rehearsal",
              "primary_run", "frozen_sha", "served_models", "test_rounds")  # fmt: skip


def resummarize(
    d: Path, *, labels: Path | None = None, utility: Path | None = None,
    judge: Path | None = None, dev_dir: Path | None = None, n_boot: int = 10_000,
) -> dict[str, Any]:  # fmt: skip
    """Reconstrói quality.json/md de uma rodada já feita, sem chamar a API."""
    old = json.loads((d / "quality.json").read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in (d / "transcripts.jsonl").read_text().splitlines() if x.strip()]
    outs = [
        Outcome(
            r["item_id"], r["student"], r["template"], r["category"], r["split"], r["status"],
            r["attempts"], tuple(r["reasons"]), r["cause"], r["expect_refusal"],
            r["refused_unduly"],
            tuple(TurnRecord(t["a"], pre_guard_answer=t["pre_guard"]) for t in r["turns"]),
        )
        for r in rows
    ]  # fmt: skip
    rr = RoundResult(outs, old["aborted"], old["abort_reason"], old["redone"])
    strata = {r["student"]: r["stratum"] for r in rows}
    meta = {k: old[k] for k in _META_KEYS if k in old}
    s = build_summary(rr, strata, meta, n_boot=n_boot, labels=labels, utility=utility, judge=judge,
                      dev_dir=dev_dir, p_meta=old.get("p_meta"))  # fmt: skip
    write_summary(d, s)
    return s


# ----------------------------------------------------------------------------- CLI
def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--split", choices=("dev", "test"))
    ap.add_argument("--model")
    ap.add_argument("--round", type=int)
    ap.add_argument("--record", action="store_true", help="registra a rodada e anota no Phoenix")
    ap.add_argument("--rehearsal", action="store_true", help="ensaio: nada é registrado/contado")
    ap.add_argument("--paraphrase", action="store_true", help="rodada secundária de robustez")
    ap.add_argument("--base-url", default=os.environ.get("EVAL_API_URL", "http://api-eval:8000"))
    ap.add_argument(
        "--data", type=Path, default=Path(os.environ.get("EVAL_DATA", ROOT / "data/dados.zip"))
    )
    ap.add_argument(
        "--holdout-yaml", type=Path, help="templates test_only decifrados (fora do git)"
    )
    ap.add_argument("--dev-json", type=Path, help="quality.json do dev (ICC para P(meta))")
    ap.add_argument("--dev-dir", type=Path, help="pasta da rodada do dev (comparativo)")
    ap.add_argument("--labels", type=Path, default=GOLD / "labels.csv")
    ap.add_argument("--utility", type=Path, default=GOLD / "utility_labels.csv")
    ap.add_argument("--judge", type=Path, help="judge.jsonl: {item_id, verdict}")
    ap.add_argument("--resummarize", type=Path, metavar="DIR", help="refaz quality.json/md offline")
    ap.add_argument("--out", type=Path, help="saída privada (teste: fora do repositório)")
    ap.add_argument(
        "--evidence", type=Path, help="docs/evidence/quality (só json/md sem perguntas)"
    )
    ap.add_argument("--phoenix-url", default=os.environ.get("PHOENIX_URL"))
    ap.add_argument("--spend-today", type=Decimal, help="gasto real do dia (obrigatório no teste)")
    ap.add_argument("--rpd-left", type=int, help="RPD restante real (obrigatório no teste)")
    ap.add_argument("--tokens-per-call", type=int, default=3000)
    ap.add_argument("--n-boot", type=int, default=N_BOOT)
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--limit", type=int, help="só os N primeiros itens (smoke; exige --rehearsal)")
    return ap.parse_args(argv)


def _inside_repo(out: Path) -> bool:
    """Repositório (ROOT), diretório de evidência e /evidence (montagem do repo no container)."""
    roots = [ROOT, Path("/evidence"), Path(os.environ.get("EVIDENCE_DIR", "/evidence"))]
    p = out.resolve()
    return any(p.is_relative_to(r.resolve()) for r in roots)


def validate_args(a: argparse.Namespace) -> str | None:
    """Sem liberdade de ajuste no teste (§4, §15): o que muda o resultado exige --rehearsal."""
    if a.resummarize:
        return None
    if not (a.split and a.model and a.round):
        return "--split, --model e --round são obrigatórios"
    if a.limit and not a.rehearsal:
        return "--limit só com --rehearsal (rodada contada roda o conjunto inteiro)"
    if a.split != "test":
        return None
    if a.out is not None and _inside_repo(a.out):
        return "teste: --out dentro do repositório/evidência vazaria o texto dos itens test_only"
    for ok, msg in (
        (not a.rehearsal, "--rehearsal é só do dev: no teste não há ensaio sem contagem"),
        (a.record, "teste exige --record (a rodada é contada)"),
        (a.dev_json, "teste exige --dev-json (P(meta) é gravada antes da rodada)"),
        (a.spend_today is not None, "teste exige --spend-today (gasto real do dia)"),
        (a.rpd_left is not None, "teste exige --rpd-left (RPD restante real)"),
        (not a.allow_dirty, "teste: sem --allow-dirty (SHA congelado)"),
        (not a.paraphrase, "teste: sem --paraphrase (rodada secundária, não é o primário)"),
        (a.n_boot == N_BOOT, f"teste: --n-boot fixo em {N_BOOT}"),
    ):
        if not ok:
            return msg
    return None


async def amain(a: argparse.Namespace) -> int:
    if err := validate_args(a):
        print(err, file=sys.stderr)
        return 2
    evidence = a.evidence or Path(os.environ.get("EVIDENCE_DIR", EVIDENCE))
    if a.resummarize:
        s = resummarize(a.resummarize, labels=a.labels, utility=a.utility, judge=a.judge,
                        dev_dir=a.dev_dir, n_boot=a.n_boot)  # fmt: skip
        publish(a.resummarize, evidence, f"{s['split']}-r{s['round']}")
        print(render_md(s))
        return 0
    try:
        sha, dirty = code_state()
    except StateError as e:
        print(e, file=sys.stderr)
        return 2
    from app.config import AppSettings, load_slo_targets
    from evals import oracle as o
    from evals.preflight import project_round
    from evals.tokens import mint

    slo, settings = load_slo_targets(), AppSettings()
    if a.model not in slo.quotas:
        print(f"modelo sem cota em slo_targets.yaml: {a.model!r}", file=sys.stderr)
        return 2
    if a.split == "test" and dirty and not a.allow_dirty:
        print("teste exige árvore limpa (SHA congelado)", file=sys.stderr)
        return 2
    counted = a.record and not a.rehearsal
    ledger = evidence / "rounds.jsonl"
    prior = [r for r in read_rounds(ledger) if r["split"] == a.split]
    if counted and any(r["round"] == a.round for r in prior):
        print(f"rodada {a.round} do {a.split} já registrada", file=sys.stderr)
        return 2
    raw = o.load_csv(a.data)
    items, pks, strata, templates = load_dataset(
        a.split, raw=raw, hmac_key=settings.hmac_key.encode(), holdout_yaml=a.holdout_yaml
    )
    if a.paraphrase:
        items = apply_paraphrases(items, templates)
    if a.limit:
        items = items[: a.limit]
    proj = project_round(
        sum(len(i.steps) for i in items), slo.admission.est_requests, a.tokens_per_call, a.model,
        spend_today=a.spend_today or Decimal(0),
        ceiling=Decimal(str(slo.limits["eval"].global_ceiling_usd)),
        rpd_left=a.rpd_left or slo.quotas[a.model].rpd,
    )  # fmt: skip
    print(f"preflight: ~US$ {proj.cost_usd:.2f}, {proj.calls} chamadas, ok={proj.ok}")
    if not proj.ok:
        return 2
    tag = f"{a.split}-r{a.round}"
    out = a.out or Path(os.environ.get("EVAL_OUT_DIR", "/eval-out")) / "quality" / tag
    out.mkdir(parents=True, exist_ok=True)
    p_meta = None
    if a.split == "test" and a.dev_json:  # gravada ANTES da execução (§4)
        dev = json.loads(a.dev_json.read_text())
        p_meta = p_meta_table(dev["icc"], dev["k"] / dev["n"], n_students=len(pks),
                              n_templates=len({i.template_id for i in items}))  # fmt: skip
        (out / "p_meta.json").write_text(json.dumps(p_meta, indent=2))
        publish(out, evidence, tag)
    frozen = frozen_sha()
    primary = a.split == "test" and counted and is_primary(frozen, sha, prior=prior)
    meta = {"split": a.split, "round": a.round, "model": a.model, "sha": sha, "dirty": dirty,
            "paraphrase": a.paraphrase, "perfil": "eval", "rehearsal": a.rehearsal,
            "frozen_sha": frozen, "primary_run": primary}  # fmt: skip
    if counted:  # ANTES de rodar: se o processo morrer, a rodada continua contada
        claim_round(ledger, {"split": a.split, "round": a.round, "sha": sha, "model": a.model,
                             "primary_run": primary, "items": len(items)})  # fmt: skip
    annotate: Annotate | None = None
    if a.record and a.phoenix_url:
        from app.obs import annotations

        annotations.configure(flush=lambda: None, send=annotations.phoenix_sender(a.phoenix_url))
        annotate = annotations.annotate_span
    client = ChatClient(a.base_url, timeout=90.0)
    try:
        rr = await run_round(items, lambda sid: mint(pks[sid], "chat"), client,
                             annotate=annotate, expect_model=a.model)  # fmt: skip
    finally:
        await client.aclose()
    if annotate:
        await annotations.drain()
    if counted:
        close_round(ledger, {"split": a.split, "round": a.round, "sha": sha, "model": a.model,
                             "aborted": rr.aborted, "abort_reason": rr.abort_reason,
                             "invalid": rr.invalid, "items": len(rr.outcomes)})  # fmt: skip
    by_id = {i.id: i for i in items}
    write_private(out, rr, by_id, strata)  # parcial também fica gravado
    meta["served_models"] = rr.served_models
    if a.split == "test":
        meta["test_rounds"] = round_states(read_rounds(ledger), "test")
        if rr.valid and not rr.aborted and a.round == 1:
            valid = _results([x for x in rr.valid if x.category != "multiturno"])
            _validation_csv(out / "scorer_validation.csv", sample_scorer_validation(valid), rr,
                            by_id)  # fmt: skip
    s = build_summary(rr, strata, meta, n_boot=a.n_boot, labels=a.labels, utility=a.utility,
                      judge=a.judge, dev_dir=a.dev_dir, p_meta=p_meta)  # fmt: skip
    write_summary(out, s)
    if not a.rehearsal:  # ensaio não vai para a evidência
        publish(out, evidence, tag)
    print(render_md(s))
    return 1 if rr.aborted else 0


def main(argv: Sequence[str] | None = None) -> None:
    sys.exit(asyncio.run(amain(parse(argv))))


if __name__ == "__main__":
    main()
