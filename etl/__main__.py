"""`python -m etl load --source <zip|dir> --database <nome>`: carga completa (padrões = compose)."""

import argparse
import sys
from pathlib import Path

from etl.config import EtlSettings
from etl.load import load_all
from etl.raw import read_raw


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(prog="etl")
    ld = ap
    ld.add_argument("--source", type=Path, default=Path("/data/dados.zip"))
    ld.add_argument("--database", default=None)
    ld.add_argument("--out", type=Path, default=Path("/out"))
    ld.add_argument("--eval-out", type=Path, default=Path("/eval-out"))
    ld.add_argument("--golden", type=Path, default=Path("tests/fixtures/golden.json"))
    ld.add_argument("--report-dir", type=Path, default=None, help="cópia extra do data-quality")
    args = ap.parse_args([a for i, a in enumerate(argv or []) if (i, a) != (0, "load")])
    from evals import oracle

    settings = EtlSettings()
    report = load_all(
        read_raw(args.source), settings, database=args.database or settings.db_name,
        out=args.out, eval_out=args.eval_out, oracle_raw=oracle.load_csv(args.source),
        golden=args.golden if args.golden.exists() else None,
    )  # fmt: skip
    if args.report_dir:
        from etl.report import write_report

        args.report_dir.mkdir(parents=True, exist_ok=True)
        write_report(report, args.report_dir)
    bad = [c.name for c in report.checks if c.memory != c.oracle]
    print(f"carga ok: {sum(report.sql_counts.values())} linhas; divergências do oráculo: {bad}")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
