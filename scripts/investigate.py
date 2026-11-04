"""`make investigate TRACE=<id>`: lê as saídas tipadas de um 👎 como ops_audit (só o não expirado).

Conexão por env: DB_HOST (127.0.0.1), DB_PORT (5432), DB_NAME (student) e
OPS_AUDIT_PASSWORD_FILE (padrão $LANE_SECRETS_DIR/ops_audit_password).
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg

from core.clock import wall_now


def fetch(conn: psycopg.Connection, trace_id: str, now: datetime) -> Any | None:
    """`app.now` é obrigatório na policy do ops_audit; linha expirada fica invisível."""
    with conn.transaction():
        conn.execute("SELECT set_config('app.now', %s, true)", (now.isoformat(),))
        row = conn.execute(
            "SELECT payload FROM agent_rw.turn_output WHERE trace_id = %s", (trace_id,)
        ).fetchone()
    return row[0] if row else None


def main() -> None:
    ap = argparse.ArgumentParser(prog="investigate")
    ap.add_argument("--trace", required=True)
    args = ap.parse_args()
    pw_file = os.environ.get("OPS_AUDIT_PASSWORD_FILE") or str(
        Path(os.environ.get("LANE_SECRETS_DIR", "/run/secrets/db")) / "ops_audit_password"
    )
    with psycopg.connect(
        host=os.environ.get("DB_HOST", "127.0.0.1"), port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ.get("DB_NAME", "student"), user="ops_audit",
        password=Path(pw_file).read_text().strip(), autocommit=True,
    ) as conn:  # fmt: skip
        payload = fetch(conn, args.trace, wall_now())
    if payload is None:
        raise SystemExit("trace não encontrado (ou expirado)")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
