"""Sensibilidade do gabarito (analysis_plan §6): `expected_tools` com nome de domínio.

O redator cego do holdout não conhecia as ferramentas e escreveu o domínio (`financeiro`,
`lms`...) em `expected_tools`. O scorer compara com o nome da ferramenta chamada, então esse
critério reprova qualquer agente. Aqui o critério impossível sai e o item vale pelos demais
motivos (fatos do oráculo, proibidos, rascunho, recusa). O primário continua o que rodou.

Uso: python -m evals.golden.domain_tools <rodada> <saída>  (rodada = pasta com transcripts.jsonl)
"""

import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any, get_args

from app.tools.base import Domain
from evals.golden.run import resummarize

_DOMAINS = frozenset(get_args(Domain))
_RX = re.compile(r"(?:t\d+: )?ferramenta esperada não chamada: (\w+)")


def drop_domain_tools(row: dict[str, Any]) -> dict[str, Any]:
    """Tira os motivos de ferramenta-domínio; sem outro motivo, o item passa."""
    keep = [r for r in row["reasons"] if not ((m := _RX.fullmatch(r)) and m[1] in _DOMAINS)]
    if len(keep) == len(row["reasons"]):
        return row
    ok = not keep
    return row | {"reasons": keep, "status": "pass" if ok else "fail",
                  "cause": None if ok else row["cause"]}  # fmt: skip


def main(src: Path, dst: Path) -> None:
    rows = [json.loads(x) for x in (src / "transcripts.jsonl").read_text().splitlines() if x]
    new = [drop_domain_tools(r) for r in rows]
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "quality.json", dst / "quality.json")
    (dst / "transcripts.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in new) + "\n", encoding="utf-8"
    )
    touched = Counter(r["template"] for r, n in zip(rows, new, strict=True) if n is not r)
    flipped = Counter(
        r["template"] for r, n in zip(rows, new, strict=True) if r["status"] != n["status"]
    )
    s = resummarize(dst)
    annex = [
        "# Anexo: itens com ferramenta-domínio (§6)",
        "",
        "| template | itens | viraram pass |",
    ]
    annex += [
        "|---|---|---|",
        *(f"| {t} | {n} | {flipped[t]} |" for t, n in sorted(touched.items())),
    ]
    (dst / "anexo.md").write_text("\n".join(annex) + "\n", encoding="utf-8")
    out = {"itens_afetados": sum(touched.values()), "viraram_pass": sum(flipped.values()),
           "primary": s["primary"], "seen_unseen": s["seen_unseen"]}  # fmt: skip
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
