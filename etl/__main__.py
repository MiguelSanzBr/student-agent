"""`python -m etl [load|purge] --source <zip|dir> --database <nome>`: carga (padrões do compose)."""

import argparse
import sys
from pathlib import Path

import psycopg

from etl.config import EtlSettings
from etl.load import load_all
from etl.raw import read_raw
from etl.report import write_report


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="etl")
    ap.add_argument("cmd", nargs="?", choices=["load", "purge"], default="load")
    ap.add_argument("--source", type=Path, default=Path("/data/dados.zip"))
    ap.add_argument("--database", default=None)
    ap.add_argument("--out", type=Path, default=Path("/out"))
    ap.add_argument("--eval-out", type=Path, default=Path("/eval-out"))
    ap.add_argument("--golden", type=Path, default=Path("tests/fixtures/golden.json"))
    ap.add_argument("--report-dir", type=Path, default=None, help="cópia extra do data-quality")
    args = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd == "purge":
        from etl.purge import purge

        settings = EtlSettings()
        n = purge(settings, database=args.database or settings.db_name)
        print(f"purga ok: {n} saídas tipadas expiradas apagadas")
        return
    if not args.golden.is_file():
        raise SystemExit(f"golden.json ausente: {args.golden} (o arquivo de login depende dele)")
    from evals import oracle

    settings = EtlSettings()
    try:
        report = load_all(
            read_raw(args.source), settings, database=args.database or settings.db_name,
            out=args.out, eval_out=args.eval_out, oracle_raw=oracle.load_csv(args.source),
            golden=args.golden,
        )  # fmt: skip
    except psycopg.Error as e:  # a mensagem do driver pode citar o valor da linha: só a classe
        raise SystemExit(
            f"carga falhou e foi desfeita: {type(e).__name__} sqlstate={e.sqlstate}"
        ) from None
    if args.report_dir:
        args.report_dir.mkdir(parents=True, exist_ok=True)
        write_report(report, args.report_dir)
    bad = [c.name for c in report.checks if not c.ok]
    print(f"carga ok: {sum(report.sql_counts.values())} linhas; divergências: {bad}")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
