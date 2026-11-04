"""Gera o esqueleto de `ontology/tables.yaml` (47 tabelas, 401 colunas) a partir dos cabeçalhos.

Destino, tipo, alvo e motivo são preenchidos à mão.
Uso: python -m scripts.gen_tables_skeleton <zip|dir>
"""

import sys
from pathlib import Path

from etl.raw import read_raw


def skeleton(source: Path) -> str:
    out = ["# [destino, tipo, alvo canon, motivo]; destino: load|transform|resolution|discard"]
    for table, rows in read_raw(source).items():
        out += [f"{table}:", "  destination: TODO", "  reason: TODO", "  columns:"]
        out += [f"    {col}: [TODO, TODO, ~, TODO]" for col in rows[0]]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.stdout.write(skeleton(Path(sys.argv[1])))
