import hashlib
import json
import random
import subprocess
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from evals import oracle as o
from evals.golden import build
from evals.golden.score import score_item
from evals.holdout import seal

ROOT = Path(__file__).parents[2]
GOLD = ROOT / "evals/golden"
HOLD = ROOT / "evals/holdout"
MINI = ROOT / "tests/fixtures/mini"
CASES = json.loads((ROOT / "tests/fixtures/golden.json").read_text())["cases"]
ENR = {f"g{c['case']}": (c["brand"], c["ra"]) for c in CASES}
QUOTAS = ("in_collection", "tem_acordo", "protocolo_aberto", "lms_manual", "transferido")


def pool(n: int = 800, seed: int = 1) -> list[dict[str, object]]:
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    for i in range(n):
        rows.append({
            "person_key": f"p{i}", "modalidade": rng.choice(["CAMPUS", "DL"]),
            "ativo": rng.random() < 0.7, "n_matriculas": rng.choice([1, 1, 1, 2]),
            "in_collection": rng.random() < 0.05, "tem_acordo": rng.random() < 0.05,
            "protocolo_aberto": rng.random() < 0.1, "lms_manual": rng.random() < 0.03,
            "transferido": rng.random() < 0.03,
            "payer_type": rng.choice(["aluno"] * 8 + ["responsavel", "empresa"]),
        })  # fmt: skip
    return rows


def test_quotas() -> None:
    sel = build.select_test_students(students=pool(), golden_keys=set())
    assert len(sel) == 40 and len({s.person_key for s in sel}) == 40
    assert sum(s.n_matriculas >= 2 for s in sel) >= 3
    for q in QUOTAS:
        assert sum(getattr(s, q) for s in sel) >= 3, q
    for p in ("responsavel", "empresa"):
        assert sum(s.payer_type == p for s in sel) >= 3, p
    strata = Counter((s.modalidade, s.ativo) for s in sel)
    assert len(strata) == 4 and min(strata.values()) >= 5
    assert build.select_test_students(students=pool(), golden_keys=set()) == sel  # semente fixa


def test_default_excludes_golden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = pool()
    gl = {"cases": [{"case": i, "person_key": r["person_key"]} for i, r in enumerate(rows[:300])]}
    (tmp_path / "golden_login.json").write_text(json.dumps(gl))
    monkeypatch.setenv("ETL_OUT_DIR", str(tmp_path))
    sel = build.select_test_students(students=rows)  # sem golden_keys: lê o golden_login.json
    assert not {s.person_key for s in sel} & {r["person_key"] for r in rows[:300]}
    monkeypatch.setenv("ETL_OUT_DIR", str(tmp_path / "vazio"))
    with pytest.raises(FileNotFoundError):  # falha alto em vez de não excluir
        build.select_test_students(students=rows)


def test_disjoint_from_golden() -> None:
    rows = pool()
    golden = {r["person_key"] for r in rows[:300]}
    sel = build.select_test_students(students=rows, golden_keys=golden)
    assert not {s.person_key for s in sel} & golden


def _manifest() -> dict[str, dict[str, object]]:
    p = HOLD / "manifest.json"
    if not p.exists():
        pytest.skip("pendente: holdout ainda não cifrado (controlador roda seal.py)")
    return json.loads(p.read_text())


def test_template_counts() -> None:
    shared = build.load_templates(GOLD / "templates.yaml")
    assert len(shared) >= 35 and {t["split"] for t in shared} == {"shared"}
    assert len({t["id"] for t in shared}) == len(shared)
    n_test_only = sum(int(v.get("test_only", 0)) for v in _manifest().values())
    assert n_test_only >= 15 and len(shared) + n_test_only >= 50


def test_holdout_only_encrypted_in_git() -> None:
    out = subprocess.run(["git", "ls-files", "evals/holdout"], cwd=ROOT, text=True,
                         capture_output=True, check=True).stdout.split()  # fmt: skip
    names = {Path(f).name for f in out}
    assert names <= {"author.pub", "manifest.json", "seal.py", "__init__.py"} | {
        n for n in names if n.endswith(".age")
    }
    ages = sorted(HOLD.glob("*.age"))
    if not ages:
        pytest.skip("pendente: holdout ainda não cifrado")
    man = _manifest()
    for a in ages:
        assert a.read_bytes().startswith(b"age-encryption.org/v1")
        assert man[a.name]["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()


def test_paraphrases_and_multiturn_files() -> None:
    ids = {t["id"] for t in build.load_templates(GOLD / "templates.yaml")}
    para = yaml.safe_load((GOLD / "paraphrases.yaml").read_text())
    assert para and set(para) <= ids and all(len(v) >= 1 for v in para.values())
    scripts = build.load_templates(GOLD / "multiturn.yaml")
    assert len(scripts) == 8 and [s["core"] for s in scripts] == [True] * 4 + [False] * 4
    assert all(len(s["turns"]) >= 2 for s in scripts)


def _items() -> tuple[list[build.DatasetItem], o.Raw]:
    raw = o.load_csv(MINI)
    shared = build.load_templates(GOLD / "templates.yaml")
    scripts = build.load_templates(GOLD / "multiturn.yaml")
    return build.build_items(shared, scripts, ENR, raw), raw


def test_required_from_oracle() -> None:
    for t in build.load_templates(GOLD / "templates.yaml"):
        assert t["required"] in ("oracle", []), t["id"]
        assert (t["required"] == "oracle") == bool(t.get("oracle_fn")), t["id"]
    items, raw = _items()
    n = 0
    for it in items:
        for st in it.steps:
            if st.oracle_fns:
                parts = [
                    o.ideal_answer({"oracle_fn": fn, "args": st.args}, raw) for fn in st.oracle_fns
                ]
                facts = [f for _, fs in parts for f in fs]
                assert list(st.required) == facts
                n += 1
            else:
                assert st.required == ()
    assert n > 100


def test_oracle_ideal_passes_all() -> None:
    items, raw = _items()
    assert len(items) > 300
    for it in items:
        r = score_item(it, build.ideal_turns(it, raw))
        assert r.passed, (it.id, r.reasons)


def test_oracle_mutated_fails() -> None:
    items, raw = _items()
    rng = random.Random(7)
    n = 0
    for it in items:
        if len(it.steps) != 1 or not it.steps[0].required:
            continue
        (t,) = build.ideal_turns(it, raw)
        try:
            bad = build.mutate_turn(t, rng)
        except ValueError:  # só fatos sem tipo mutável (ex.: "2028/2")
            continue
        assert not score_item(it, [bad]).passed, (it.id, t.answer, bad.answer)
        n += 1
    assert n > 80


def test_hash_covers_plan_score_and_ciphertext(tmp_path: Path) -> None:
    lines = dict(
        reversed(ln.split("  ", 1)) for ln in (GOLD / "dataset.sha256").read_text().splitlines()
    )
    need = {
        "evals/golden/analysis_plan.md", "evals/golden/score.py", "evals/golden/templates.yaml",
        "evals/golden/build.py", "evals/oracle.py", "evals/holdout/manifest.json",
        "evals/holdout/author.pub",
    }  # fmt: skip
    need |= {f"evals/holdout/{a.name}" for a in HOLD.glob("*.age")}
    assert need <= set(lines)
    for path, digest in lines.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest, path
    ots = GOLD / "dataset.sha256.ots"
    if not ots.exists():
        pytest.skip("pendente: carimbo OpenTimestamps (make stamp)")


def test_oracle_check_csv_has_20_blank() -> None:
    import csv

    rows = list(csv.DictReader((GOLD / "oracle_check.csv").open(encoding="utf-8")))
    assert len(rows) == 20 and len({r["item_id"] for r in rows}) == 20
    assert all(r["conferido"] == "" for r in rows)


def test_seal_roundtrip(tmp_path: Path) -> None:
    from pyrage import x25519

    ident = x25519.Identity.generate()
    pub = tmp_path / "author.pub"
    pub.write_text(str(ident.to_public()) + "\n")
    plain = tmp_path / "templates_test_only.yaml"
    tpls = [{"id": f"h{i}", "split": "test_only", "text": f"pergunta {i}"} for i in range(16)]
    plain.write_text(yaml.safe_dump({"templates": tpls}))
    out = tmp_path / "holdout"
    out.mkdir()
    (out / "manifest.json").write_text(json.dumps({"attacks_blind.yaml.age": {"sha256": "x"}}))
    seal.seal(plain, pub, out)
    age = out / "templates_test_only.yaml.age"
    data = age.read_bytes()
    assert data.startswith(b"age-encryption.org/v1") and b"pergunta" not in data
    man = json.loads((out / "manifest.json").read_text())
    assert man["templates_test_only.yaml.age"] == {
        "sha256": hashlib.sha256(data).hexdigest(), "test_only": 16,
        "plain_sha256": hashlib.sha256(plain.read_bytes()).hexdigest(),
    }  # fmt: skip
    assert man["attacks_blind.yaml.age"] == {"sha256": "x"}  # outras entradas intactas
    import pyrage

    assert pyrage.decrypt(data, [ident]) == plain.read_bytes()
    plain.write_text(yaml.safe_dump({"templates": [{**tpls[0], "split": "shared"}]}))
    with pytest.raises(ValueError, match="test_only"):
        seal.seal(plain, pub, out)


def test_make_stamp_target() -> None:
    mk = (ROOT / "Makefile").read_text()
    assert "stamp:" in mk and "ots stamp evals/golden/dataset.sha256" in mk


def test_mutated_categorical_and_multiturn_fail() -> None:
    items, raw = _items()
    status = set(o.ENROLLMENT_STATUS_PT.values())
    n = 0
    for it in items:
        if len(it.steps) != 1:
            continue
        (t,) = build.ideal_turns(it, raw)
        for f in it.steps[0].required:
            if f in status:
                other = next(x for x in sorted(status) if x != f)
                bad = t.__class__(**{**t.__dict__, "answer": t.answer.replace(f, other)})
                assert not score_item(it, [bad]).passed, it.id
                n += 1
    assert n > 10
    n_multi = 0
    for it in (i for i in items if len(i.steps) > 1):
        good = build.ideal_turns(it, raw)
        assert score_item(it, good).passed, it.id
        for k, st in enumerate(it.steps):
            if not st.required:
                continue
            try:
                bad = [*good[:k], build.mutate_turn(good[k], random.Random(3)), *good[k + 1 :]]
            except ValueError:
                continue
            assert not score_item(it, bad).passed, it.id  # 1 turno errado reprova o item todo
            n_multi += 1
            break
    assert n_multi >= 8


def test_wrong_status_and_other_enrollment_forbidden() -> None:
    items, raw = _items()
    by_id = {i.id: i for i in items}
    t1 = by_id["ac-01@g1"]  # 1 matrícula ativa
    assert any("trancada" in f for f in t1.steps[0].forbidden)
    (good,) = build.ideal_turns(t1, raw)
    for extra in (" Não está trancada.", " Não está cancelada nem inativa."):
        ok = replace(good, answer=good.answer + extra)
        assert score_item(t1, [ok]).passed, extra
    bad = replace(good, answer=good.answer.replace("ativa", "trancada"))
    assert not score_item(t1, [bad]).passed
    # 2 matrículas: resposta correta que lista as duas passa (regra da outra matrícula desligada)
    t3 = by_id["fi-01@g3"]
    other_enr = ["UniSaoPaulo", "181544"]
    other = o.ideal_answer({"oracle_fn": "oracle_financial", "args": {"enr": other_enr}}, raw)[0]
    (g3,) = build.ideal_turns(t3, raw)
    both = replace(g3, answer=f"{g3.answer} Na outra matrícula: {other}")
    assert score_item(t3, [both]).passed
    # com `escopo: uma_matricula` os valores exclusivos da outra matrícula viram proibidos
    tpl = {**next(t for t in build.load_templates(GOLD / "templates.yaml") if t["id"] == "ac-01"),
           "escopo": "uma_matricula"}  # fmt: skip
    (scoped,) = build.build_items([tpl], [], {"g3": ENR["g3"]}, raw)
    (mine,) = build.ideal_turns(scoped, raw)
    theirs = o.ideal_answer({"oracle_fn": "oracle_courses", "args": {"enr": other_enr}}, raw)[0]
    assert score_item(scoped, [mine]).passed
    assert not score_item(scoped, [replace(mine, answer=f"{mine.answer} {theirs}")]).passed
