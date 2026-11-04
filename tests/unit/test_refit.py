import json
import random
import shutil
from pathlib import Path

import pytest

from app.config import SLO_PATH, load_slo_targets

ROOT = Path(__file__).resolve().parents[2]


def _rows(n: int = 40) -> list[dict]:
    rng = random.Random(3)
    rows = []
    for i in range(n):
        calls = [
            ["tool_call", rng.lognormvariate(1.2, 0.3)],
            ["tool_call", rng.lognormvariate(1.2, 0.3)],
            ["answer_call", rng.lognormvariate(1.5, 0.3)],
        ]
        ovh = rng.lognormvariate(-1.0, 0.2)
        total = sum(s for _, s in calls) + ovh
        rows.append(
            {
                "student": f"s{i % 5}",
                "cls": "2+",
                "status": "ok",
                "total_s": total,
                "ttfe_s": calls[0][1],
                "llm_calls_s": calls,
                "overhead_s": ovh,
                "model": "claude-haiku-5-5",
            }
        )
    return rows


def _write(p: Path, rows: list[dict]) -> Path:
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_load_samples_per_call_and_censoring(tmp_path: Path) -> None:
    from evals.latency.refit import load_samples

    rows = _rows(35)
    rows[0]["status"] = "budget_exceeded"
    rows[1]["status"] = "llm_unavailable"
    comps, excluded, models = load_samples(_write(tmp_path / "s.jsonl", rows))
    assert excluded == 2 and models == {"claude-haiku-5-5"}
    assert len(comps["tool_call"]) == 2 * 33 and len(comps["answer_call"]) == 33
    assert len(comps["overhead"]) == 33


def test_degraded_turns_are_complete_samples(tmp_path: Path) -> None:
    # guarda degradou a resposta, mas o turno terminou: o tempo é válido e é cauda real
    from evals.latency.refit import load_samples

    rows = _rows(35)
    rows[0]["status"] = "degraded"
    rows[1]["status"] = "error"
    comps, excluded, _ = load_samples(_write(tmp_path / "s.jsonl", rows))
    assert excluded == 1 and len(comps["overhead"]) == 34


PRIOR = (
    Path(__file__).parents[1] / "fixtures" / "slo_targets_prior.yaml"
)  # yaml anterior ao reajuste


def _slo_copy(tmp_path: Path) -> Path:
    return Path(shutil.copy(PRIOR, tmp_path / "slo_targets.yaml"))


def test_repo_slo_already_refitted_and_new_refit_is_refused(tmp_path: Path) -> None:
    from evals.latency.refit import main

    assert SLO_PATH.read_text().count("reajuste único da janela A") == 4
    slo = Path(shutil.copy(SLO_PATH, tmp_path / "slo_targets.yaml"))
    s = str(_write(tmp_path / "s.jsonl", _rows()))
    with pytest.raises(SystemExit, match="já reajustado"):
        main(["--samples", s, "--slo", str(slo)])
    assert slo.read_text() == SLO_PATH.read_text()


def test_refit_edits_only_four_sections_and_keeps_comments(tmp_path: Path, capsys) -> None:
    from evals.latency.refit import main

    slo = _slo_copy(tmp_path)
    before = slo.read_text()
    assert main(["--samples", str(_write(tmp_path / "s.jsonl", _rows())), "--slo", str(slo)]) == 0
    after = slo.read_text()
    old, new = load_slo_targets(PRIOR), load_slo_targets(slo)
    assert new.components["tool_call"] != old.components["tool_call"]
    assert new.graph_budget_s != old.graph_budget_s
    assert new.targets != old.targets and new.ttfe != old.ttfe
    # o resto do arquivo (e os comentários) fica idêntico
    changed = {"components", "targets", "ttfe", "graph_budget_s"}
    for k, v in old.model_dump().items():
        if k not in changed:
            assert new.model_dump()[k] == v, k
    keep, inside = [], False
    for line in before.splitlines():
        if line[:1] not in (" ", "#", "\t", ""):
            inside = line.startswith(("components:", "targets:", "ttfe:", "graph_budget_s:"))
        if not inside:
            keep.append(line)
    assert [x for x in after.splitlines() if x in keep] == keep  # mesmas linhas, mesma ordem
    assert after.count("reajuste único da janela A") == 4
    assert "claude-haiku-5-5" in after and "n=" in after
    out = capsys.readouterr().out
    assert "antes" in out and "depois" in out and "excluídos: 0" in out


def test_second_refit_is_refused(tmp_path: Path) -> None:
    from evals.latency.refit import main

    slo = _slo_copy(tmp_path)
    s = str(_write(tmp_path / "s.jsonl", _rows()))
    assert main(["--samples", s, "--slo", str(slo)]) == 0
    once = slo.read_text()
    with pytest.raises(SystemExit, match="já reajustado"):
        main(["--samples", s, "--slo", str(slo)])
    assert slo.read_text() == once


def test_too_few_samples_fails_without_writing(tmp_path: Path) -> None:
    from evals.latency.refit import main

    slo = _slo_copy(tmp_path)
    before = slo.read_text()
    with pytest.raises(ValueError, match="insuficientes"):
        main(["--samples", str(_write(tmp_path / "s.jsonl", _rows(10))), "--slo", str(slo)])
    assert slo.read_text() == before


def test_makefile_target_refit_window_a() -> None:
    mk = (ROOT / "Makefile").read_text()
    assert "refit-window-a:" in mk and "evals.latency.refit --samples $(SAMPLES)" in mk
    assert "refit-window-a" in mk.split("\n", 1)[0] + mk.split("\n", 2)[1]  # .PHONY
    assert "SAMPLES" in mk.split("refit-window-a:")[1].split("\n")[1]  # exige SAMPLES=


def test_publish_evidence_copies_samples() -> None:
    from scripts.publish_evidence import PUBLIC

    assert PUBLIC.fullmatch("latency-A-samples.jsonl") and not PUBLIC.fullmatch("results.jsonl")
