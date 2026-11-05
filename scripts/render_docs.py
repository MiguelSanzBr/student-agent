"""Renderiza os números das evidências nos marcadores `<!-- num:chave -->…<!-- /num -->`."""

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
DOCS = [ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md", EVIDENCE / "README.md"]

MARKER = re.compile(r"<!-- num:([\w.\-]+) -->(.*?)<!-- /num -->", re.S)
COMMENT = re.compile(r"<!--.*?-->", re.S)
# sem \w antes: "D17" e "T22" são identificadores, não números
TOKEN = re.compile(r"(?<!\w)-?\d[\d.,]*\d|(?<!\w)\d")


def _load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _pct(x: float) -> str:
    return f"{x:.1%}".replace(".", ",")


def _ci(c: Mapping[str, Any] | None, what: str) -> str:
    """Ponto e IC 95% (formato dos runners: point/low/high); sem IC, diz que é indisponível."""
    if not c:
        raise KeyError(f"{what}: sem número (None)")
    if "low" not in c:
        return f"{_pct(c['point'])} (IC indisponível)"
    return f"{_pct(c['point'])} [{_pct(c['low'])}; {_pct(c['high'])}]"


def _templates(g: Mapping[str, Any]) -> str:
    """Grupo do redteam (`_group`): templates que falharam, com o Wilson dos templates."""
    lo, hi = g["wilson"]
    return f"{g['failed']}/{g['templates']} templates (Wilson 95%: {_pct(lo)} a {_pct(hi)})"


def _s(v: float) -> str:
    return f"{v:.2f} s".replace(".", ",")


def _rounds(evidence_dir: Path, split: str) -> dict[int, Any]:
    """quality.json de cada rodada `<split>-rN` que não é ensaio, por N."""
    found = {}
    for f in (evidence_dir / "quality").glob(f"{split}-r*/quality.json"):
        q = json.loads(f.read_text())
        if (m := re.fullmatch(rf"{split}-r(\d+)", f.parent.name)) and not q.get("rehearsal"):
            found[int(m[1])] = q
    return found


def _round(evidence_dir: Path, split: str) -> Any:
    """A maior rodada `<split>-rN` que não é ensaio; ausente = None."""
    found = _rounds(evidence_dir, split)
    return found[max(found)] if found else None


def _quality(evidence_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for n, q in sorted(_rounds(evidence_dir, "dev").items()):
        out[f"quality.dev.r{n}.pass1"] = _ci(q["primary"], f"dev-r{n}.primary")
    if (dev := _round(evidence_dir, "dev")) is not None:
        out["quality.dev.pass1"] = _ci(dev["primary"], "dev.primary")
    if (t := _round(evidence_dir, "test")) is not None:
        su, ref = t["seen_unseen"], t["refusal"]
        wrong = ref["indevida_ausente"]  # no JSON é a taxa de NÃO recusar indevidamente
        inv = {"point": 1 - wrong["point"]} | (
            {"low": 1 - wrong["high"], "high": 1 - wrong["low"]} if "low" in wrong else {}
        )
        out |= {
            "quality.test.pass1": _ci(t["primary"], "test.primary"),
            "quality.seen.pass1": _ci(su["vistos(shared)"], "vistos"),
            "quality.unseen.pass1": _ci(su["nao_vistos(test_only)"], "nao_vistos"),
            "quality.test.refusal_ok": _ci(ref["correta"], "refusal.correta"),
            "quality.test.refusal_wrong": _ci(inv, "refusal.indevida"),
            "quality.test.cross": _ci(ref["cruzamento"], "refusal.cruzamento"),
        }
        d = evidence_dir / "quality" / f"test-r{t['round']}"
        if (r := _load(d / "quality.resummarized.json")) and (sens := r.get("sensitivity")):
            keys = (("corrected", "pass_corrigido"), ("vpp", "vpp"), ("vpn", "vpn"))
            out |= {f"quality.test.{k}": _ci(sens[f], f"sensitivity.{f}") for k, f in keys}
        if (f := _load(d / "ferramenta-dominio" / "quality.json")) is not None:  # §6: sensibilidade
            out["quality.test.domain_tools"] = _ci(f["primary"], "ferramenta-dominio.primary")
    return out


def _security(sec: Mapping[str, Any]) -> dict[str, str]:
    out = {
        "security.dev.behavior.success": _templates(sec["behavior"]),
        "security.leak.success": _templates(sec["leak"]),
    }
    if (
        "holdout" in sec
    ):  # a frente do holdout cego grava `holdout` com a forma de `behavior`/`leak`
        out["security.holdout.behavior.success"] = _templates(sec["holdout"]["behavior"])
    return out


def _latency(a: Mapping[str, Any], load: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {
        "latency.cost.per_turn": f"US$ {a['cost_per_turn_usd']:.4f}".replace(".", ","),
        "latency.budget_exceeded": _pct(a["budget_exceeded_rate"]),
    }
    for cls, name in (("card", "card"), ("1", "one"), ("2+", "two_plus")):
        c = a["classes"][cls]
        q = c["quantiles"]
        out |= {
            f"latency.{name}.p50": _s(q["p50"]["value"]),
            f"latency.{name}.p95": _s(q["p95"]["value"]),
            f"latency.{name}.ttfe_p95": _s(c["ttfe_p95"]["value"]),
        }
    out["latency.overhead.p95"] = _s(a["classes"]["2+"]["cascade"]["overhead"]["p95"])
    if load is not None:
        ok = [s["ok"] for s in load["steps"]]
        out["latency.load.plateau"] = (
            f"escada (a) {'aprovada' if load['pass'] else 'reprovada'}: {ok[-1]} turnos admitidos "
            f"no degrau mais alto, pico de {max(ok)}"
        )
    return out


def build_numbers(evidence_dir: Path) -> dict[str, str]:
    """Chaves estáveis → texto. Arquivo ausente = chaves ausentes (render_docs reclama).

    Fontes (saídas reais dos runners, copiadas para docs/evidence por `make publish-evidence`):
    quality/<dev|test>-rN/quality.json (maior N sem ensaio; `make eval-dev|eval-test`),
    quality/guard/guard.json (`make eval-guard`), security.json (`make eval-security`, `holdout`
    vem do holdout cego), latency-A.json (janela A de `make eval-latency`),
    load-a-w1.json (`make load-test`)."""
    out: dict[str, str] = _quality(evidence_dir)
    if (g := _load(evidence_dir / "quality" / "guard" / "guard.json")) is not None:
        out["guard.blind_recall"] = _ci(g["blind"]["recall"], "guard.recall")
        fb = g["blind"]["false_block"]
        out["guard.false_block"] = f"{fb['k']}/{fb['n']} ({_ci(fb, 'guard.false_block')})"
        by_t = g["blind"].get("recall_by_template", {})
        for t in ("status_word", "flip_negation"):
            if t in by_t:
                out[f"guard.weak.{t}"] = f"{by_t[t]['k']}/{by_t[t]['n']}"
    if (sec := _load(evidence_dir / "security.json")) is not None:
        out |= _security(sec)
    if (lat := _load(evidence_dir / "latency-A.json")) is not None:
        if lat.get("simulated"):
            raise ValueError("latency-A.json tem simulated=true: ensaio, não medição")
        out |= _latency(lat, _load(evidence_dir / "load-a-w1.json"))
    if (dq := _load(evidence_dir / "data-quality.json")) is not None:
        checks = {c["name"]: c["legs"] for c in dq["checks"]}

        def agreed(name: str) -> Any:
            """Valor em que todas as pernas do número concordam; divergência é erro."""
            legs = checks[name]
            first = next(iter(legs.values()))
            if any(v != first for v in legs.values()):
                raise ValueError(f"pernas divergem em '{name}': {legs}")
            return first

        out |= {
            "data.persons": str(agreed("pessoas")),
            "data.enrollments": str(agreed("matrículas")),
            "data.contracts": str(agreed("contratos resolvidos")),
            "data.protocols_linked": str(agreed("protocolos ligados")),
            "data.protocols_unlinked": str(agreed("protocolos sem vínculo")),
            "data.lms_accounts": str(agreed("contas LMS ligadas")),
            "data.protocol_ref": str(agreed("PROTOCOL_REF")[0]),
            "data.snapshot": str(agreed("snapshot do último mês")[0]),
            "data.logins_absent": str(agreed("logins de analista ausentes")),
            "data.lms_attempts": str(agreed("tentativas do LMS")),
            "data.simulados_abertos": str(agreed("simulados abertos sem tentativa")),
            "data.simulados_restantes": str(agreed("simulados abertos com tentativa restante")),
            "data.provas_sem_tentativa": str(agreed("provas com nota encerradas sem tentativa")),
        }
        out |= {f"data.rule.{k}": str(v) for k, v in agreed("protocolos por regra").items()}
    if (sp := _load(evidence_dir / "spikes.json")) is not None:
        for name, code in sp["a2a"]["codes"].items():
            out[f"a2a.{re.sub(r'[^\w]+', '_', name).strip('_')}"] = str(code)
    return out


def render_docs(
    paths: Sequence[Path], numbers: Mapping[str, str], allow_missing: bool = False
) -> list[str]:
    """Troca o miolo de cada marcador. Chave ausente → KeyError antes de gravar qualquer arquivo;
    com `allow_missing`, mantém o miolo atual desses marcadores. Devolve as chaves faltantes."""
    texts = {p: p.read_text() for p in paths}
    missing = sorted(
        {k for text in texts.values() for k, _ in MARKER.findall(text) if k not in numbers}
    )
    if missing and not allow_missing:
        raise KeyError(f"chave sem número: {', '.join(missing)}")
    for p, text in texts.items():
        p.write_text(MARKER.sub(lambda m: _keep(m, numbers), text))
    return missing


def _keep(m: re.Match[str], numbers: Mapping[str, str]) -> str:
    return f"<!-- num:{m[1]} -->{numbers.get(m[1], m[2])}<!-- /num -->"


def _norm(s: str) -> str:
    return re.sub(r"[.,]", "", s)


def find_hand_typed(text: str, numbers: Mapping[str, str]) -> list[str]:
    """Tokens numéricos fora de marcador iguais a algum valor de `numbers` (≥ 2 dígitos)."""
    forbidden = {
        _norm(v)
        for v in numbers.values()
        if re.fullmatch(r"-?\d[\d.,]*", v) and len(re.sub(r"\D", "", v)) >= 2
    }
    bare = COMMENT.sub("", MARKER.sub("", text))
    return [t for t in TOKEN.findall(bare) if _norm(t) in forbidden]


def main() -> int:
    numbers = build_numbers(EVIDENCE)
    (EVIDENCE / "numbers.json").write_text(
        json.dumps(numbers, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    missing = render_docs(DOCS, numbers, allow_missing="--allow-missing" in sys.argv[1:])
    if missing:
        print("a medir (mantidos):", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
