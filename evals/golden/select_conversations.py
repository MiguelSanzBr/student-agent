"""Sorteia 30 conversas da rodada para o autor julgar a utilidade (spec §6.4; should).

    uv run python -m evals.golden.select_conversations docs/evidence/quality/test-r1

Lê `transcripts.jsonl` da rodada; escreve `conversations.md` (texto para ler) e
`utility_labels.csv` (item_id,util: o autor preenche s/n) na mesma pasta.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from evals.golden.run import VALIDATION_SEED, sample_utility


def select(round_dir: Path, *, seed: int = VALIDATION_SEED, n: int = 30) -> list[str]:
    rows = [json.loads(x) for x in (round_dir / "transcripts.jsonl").read_text().splitlines()]
    rows = sorted((r for r in rows if r["turns"]), key=lambda r: r["item_id"])
    pick = sample_utility(rows, n, seed=seed)
    md = ["# Conversas para julgar a utilidade", "",
          "Marque em `utility_labels.csv` (coluna `util`): s = útil, n = não útil.", ""]  # fmt: skip  # noqa: E501
    for r in pick:
        md.append(f"## {r['item_id']}")
        for t in r["turns"]:
            md += [f"**Aluno:** {t['q']}", "", f"**Agente:** {t['a']}", ""]
    (round_dir / "conversations.md").write_text("\n".join(md), encoding="utf-8")
    with (round_dir / "utility_labels.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["item_id", "util"])
        w.writerows([[r["item_id"], ""] for r in pick])
    return [r["item_id"] for r in pick]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(f"{len(select(Path(sys.argv[1])))} conversas em {sys.argv[1]}/conversations.md")
