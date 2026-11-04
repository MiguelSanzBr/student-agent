"""Fixture `db`: banco descartável, criado do zero (sem TEMPLATE), no Postgres efêmero da raia.

Variáveis: DB_PORT (ops/lanes.env) e LANE_SECRETS_DIR (impresso por `make lane-up LANE=x`).
Sem elas os testes são pulados.
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

ETL = Path(__file__).resolve().parents[2] / "etl"
ROLES = ("etl_owner", "agent_app", "ops_audit")


@dataclass(frozen=True)
class Db:
    name: str
    port: str
    secrets: Path

    def connect(self, role: str = "etl_owner", *, autocommit: bool = True) -> psycopg.Connection:
        user = "postgres" if role == "postgres" else role
        password = (self.secrets / f"{user}_password").read_text().strip()
        return psycopg.connect(
            host="127.0.0.1",
            port=int(self.port),
            dbname=self.name,
            user=user,
            password=password,
            autocommit=autocommit,
        )

    def apply(self, *files: str, role: str = "etl_owner") -> None:
        with self.connect(role) as c:
            for f in files:
                c.execute((ETL / f).read_bytes().decode())  # type: ignore[call-overload]


@pytest.fixture
def db() -> Iterator[Db]:
    port, secrets = os.environ.get("DB_PORT"), os.environ.get("LANE_SECRETS_DIR")
    if not port or not secrets:
        pytest.skip("sem DB_PORT/LANE_SECRETS_DIR (make lane-up LANE=x)")
    name = f"t_{uuid.uuid4().hex[:12]}"
    d = Db(name, port, Path(secrets))
    pw = (d.secrets / "postgres_password").read_text().strip()
    admin = psycopg.connect(
        host="127.0.0.1", port=int(port), dbname="postgres", user="postgres", password=pw,
        autocommit=True,
    )  # fmt: skip
    admin.execute(f'CREATE DATABASE "{name}"')  # type: ignore[call-overload]
    try:
        # mesmos privilégios de banco que ops/postgres/init.sh dá ao banco `student`
        with d.connect("postgres") as c:
            c.execute(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC')  # type: ignore[call-overload]
            c.execute(f'GRANT CONNECT ON DATABASE "{name}" TO {", ".join(ROLES)}')  # type: ignore[call-overload]
            c.execute(f'GRANT CREATE ON DATABASE "{name}" TO etl_owner')  # type: ignore[call-overload]
        d.apply("util.sql", "schema.sql")
        yield d
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')  # type: ignore[call-overload]
        admin.close()
