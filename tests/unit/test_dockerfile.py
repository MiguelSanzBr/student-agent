import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _stages() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur = ""
    for ln in (ROOT / "Dockerfile").read_text().splitlines():
        if m := re.match(r"FROM\s+\S+\s+AS\s+(\w+)", ln):
            cur = m.group(1)
            out[cur] = []
        elif cur:
            out[cur].append(ln)
    return out


def test_golden_cpfs_nunca_na_imagem_da_api() -> None:
    st = _stages()
    com_golden = {
        k for k, v in st.items() if any("golden.json" in ln for ln in v if not ln.startswith("#"))
    }
    assert com_golden == {"etl", "eval"}  # quem lê; base (e portanto api) não leva


def test_compose_etl_usa_o_estagio_etl() -> None:
    svc = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["etl"]
    assert svc["build"]["target"] == "etl"
