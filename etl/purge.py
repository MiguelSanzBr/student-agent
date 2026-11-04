"""Purga das saídas tipadas do 👎 vencidas (agent_rw.turn_output), como etl_owner."""

from datetime import datetime

import psycopg

from core.clock import wall_now
from etl.config import EtlSettings


def purge(settings: EtlSettings, *, database: str, now: datetime | None = None) -> int:
    """Apaga o que expirou; a policy de DELETE exige `app.now` (sem missing_ok: esquecer falha)."""
    with psycopg.connect(
        host=settings.db_host, port=settings.db_port, dbname=database, user="etl_owner",
        password=settings.etl_owner_password,
    ) as conn:  # fmt: skip
        conn.execute("SELECT set_config('app.now', %s, true)", ((now or wall_now()).isoformat(),))
        n = conn.execute("DELETE FROM agent_rw.turn_output").rowcount
    return n
