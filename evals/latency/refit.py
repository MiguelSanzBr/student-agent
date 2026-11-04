"""Reajuste único da janela A (spec §6.5): amostras por chamada -> `slo_targets.yaml`.

    python -m evals.latency.refit --samples docs/evidence/latency-A-samples.jsonl

Só `components`, `targets`, `ttfe` e `graph_budget_s` mudam, por edição textual (comentários e o
resto do arquivo ficam intactos). `frozen` fica como está (o M4 congela); o 2º reajuste é recusado
pelo marcador `MARK` nas seções, além do `frozen` que `refit_after_window_a` já recusa."""

import argparse
import json
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from app.config import SLO_PATH, SloTargets, load_slo_targets
from evals.latency.formula import refit_after_window_a

MARK = "reajuste único da janela A"
SECTIONS = ("components", "targets", "ttfe", "graph_budget_s")


def load_samples(path: Path) -> tuple[dict[str, list[float]], int, set[str]]:
    """(amostras por componente, turnos excluídos, modelos). Censura: só `status == ok` entra;
    prazo estourado, LLM indisponível e 429 cortariam a cauda. Chamadas são por chamada;
    `overhead`, por turno."""
    comps: dict[str, list[float]] = defaultdict(list)
    excluded, models = 0, set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["status"] != "ok":
            excluded += 1
            continue
        models.add(r.get("model", ""))
        for kind, s in r["llm_calls_s"]:
            comps[kind].append(s)
        comps["overhead"].append(r["overhead_s"])
    return dict(comps), excluded, models


def _flow(d: dict[str, float]) -> str:
    return "{" + ", ".join(f"{k}: {v}" for k, v in d.items()) + "}"


def _render(key: str, new: SloTargets, note: str) -> str:
    if key == "graph_budget_s":
        return f"graph_budget_s: {new.graph_budget_s}  # {note}\n"
    head = f"{key}:  # {note}\n"
    if key == "components":
        rows = {
            k: {"mu": round(v.mu, 4), "sigma": round(v.sigma, 4)} for k, v in new.components.items()
        }
    elif key == "targets":
        rows = {c: q.model_dump() for c, q in new.targets.items()}
    else:
        rows = {c: t.model_dump() for c, t in new.ttfe.items()}
    return head + "".join(
        f"  {c if c.isidentifier() else json.dumps(c)}: {_flow(v)}\n" for c, v in rows.items()
    )


def edit_text(text: str, new: SloTargets, note: str) -> str:
    """Troca só as 4 seções de topo; o fim de cada uma é a próxima chave de topo (comentários de
    coluna 0 logo antes dela pertencem à próxima)."""
    lines = text.splitlines(keepends=True)
    for key in SECTIONS:
        i = next((n for n, x in enumerate(lines) if re.match(rf"{key}:", x)), None)
        if i is None:
            raise ValueError(f"seção {key!r} ausente")
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or lines[j][0] in " \t#"):
            j += 1
        while j > i + 1 and (not lines[j - 1].strip() or lines[j - 1].startswith("#")):
            j -= 1
        lines[i:j] = [_render(key, new, note)]
    return "".join(lines)


def _show(title: str, s: SloTargets) -> None:
    print(f"{title}:")
    for k, v in s.components.items():
        print(f"  {k}: mu={v.mu:.4f} sigma={v.sigma:.4f}")
    for c, q in s.targets.items():
        print(f"  {c}: p50={q.p50} p90={q.p90} p95={q.p95} p99={q.p99} ttfe_p95={s.ttfe[c].p95}")
    print(f"  graph_budget_s={s.graph_budget_s}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="refit")
    ap.add_argument("--samples", type=Path, required=True)
    ap.add_argument("--slo", type=Path, default=SLO_PATH)
    a = ap.parse_args(argv)
    text = a.slo.read_text()
    if MARK in text:
        raise SystemExit("slo_targets.yaml já reajustado pela janela A: não há segundo refit")
    comps, excluded, models = load_samples(a.samples)
    cur = load_slo_targets(a.slo)
    new = refit_after_window_a(comps, cur)  # erra com < 30 por componente ou `frozen`
    n = "/".join(f"{k} {len(v)}" for k, v in sorted(comps.items()))
    note = f"{MARK} ({date.today().isoformat()}, {'+'.join(sorted(models))}, n={n})"
    a.slo.write_text(edit_text(text, new, note))
    print(f"turnos excluídos: {excluded} (status != ok)")
    _show("antes", cur)
    _show("depois", new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
