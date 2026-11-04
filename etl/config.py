from typing import ClassVar

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import SecretOnlyFromFile, check_secret


class EtlSettings(SecretOnlyFromFile, BaseSettings):
    model_config = SettingsConfigDict(
        secrets_dir="/run/secrets/etl", extra="ignore", hide_input_in_errors=True
    )
    SECRETS: ClassVar[frozenset[str]] = frozenset({"etl_owner_password", "hmac_key"})

    etl_owner_password: str
    hmac_key: str
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "student"

    @field_validator("etl_owner_password", "hmac_key")
    @classmethod
    def _secret_ok(cls, v: str) -> str:
        return check_secret(v)
