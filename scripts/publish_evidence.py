"""Copia as saídas públicas dos runners (eval-out) para docs/evidence (`make publish-evidence`).

Lista fechada: nada fora dela sai do volume (results*.jsonl, label_sample*, forbidden_map*,
transcripts e o resto são sensíveis, só no eval-out). Nomes são os que `render_docs` lê."""

import re
import shutil
import sys
from pathlib import Path

PUBLIC = re.compile(r"security\.(json|md)|latency-[ABC](-samples\.jsonl|\.json)|load-a-w\d+\.json")


def publish(src: Path, dst: Path) -> list[str]:
    """Copia (sem preservar modo) os arquivos públicos de `src` (só o nível raiz) para `dst`."""
    done = []
    for f in sorted(src.iterdir()):
        if f.is_file() and PUBLIC.fullmatch(f.name):
            shutil.copyfile(f, dst / f.name)
            (dst / f.name).chmod(0o644)  # legível pelo autor no host (uid do container difere)
            done.append(f.name)
    return done


if __name__ == "__main__":
    print("\n".join(publish(Path(sys.argv[1]), Path(sys.argv[2]))))
