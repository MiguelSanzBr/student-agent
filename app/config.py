from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Literal, Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SecretsSettingsSource,
    SettingsConfigDict,
)

SLO_PATH = Path(__file__).resolve().parent.parent / "slo_targets.yaml"
MIN_SECRET = 24


class Quota(BaseModel):
    rpm: int
    tpm: int
    rpd: int


class Admission(BaseModel):
    quota_fraction: float = 0.9
    est_requests: int  # E[chamadas ao LLM por turno], para o reserve do balde
    est_tokens: int  # E[tokens por turno], idem


class Lognormal(BaseModel):
    mu: float  # ln(mediana em s)
    sigma: float


class Limits(BaseModel):
    input_chars: int
    answer_chars: int
    msgs_per_min: int
    tokens_per_day: int
    global_ceiling_usd: float
    history_turns: int
    history_ttl_s: int
    jwt_ttl_s: int
    draft_ttl_s: int
    login_ticket_ttl_s: int
    a2a_task_ttl_s: int
    max_rounds: int
    degrade_at: float | None  # None: nunca degrada (perfil eval)
    degrade_rounds: int


class Quantiles(BaseModel):
    p50: float
    p90: float
    p95: float
    p99: float


class Ttfe(BaseModel):
    p95: float


class SloTargets(BaseModel):
    frozen: bool
    quotas: dict[str, Quota]
    admission: Admission
    components: Mapping[str, Lognormal]
    limits: Mapping[Literal["default", "eval"], Limits]
    targets: dict[str, Quantiles]
    ttfe: dict[str, Ttfe]
    graph_budget_s: float
    buckets: list[float]
    turn_slots: int
    infra_overhead_p95_ms: int = 300


def load_slo_targets(path: Path = SLO_PATH) -> SloTargets:
    return SloTargets.model_validate(yaml.safe_load(path.read_text()))


def check_secret(v: str) -> str:
    low = v.lower()
    if len(v) < MIN_SECRET or "change-me" in low or "example" in low:
        raise ValueError(f"segredo vazio, curto (< {MIN_SECRET}) ou de exemplo")
    return v


class SecretOnlyFromFile:
    """Mixin: os campos em SECRETS só entram pelo secrets_dir (ou init), nunca pelo ambiente."""

    SECRETS: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        secrets = cls.SECRETS

        class _Env(EnvSettingsSource):
            def __call__(self) -> dict[str, Any]:
                return {k: v for k, v in super().__call__().items() if k not in secrets}

        return (
            init_settings,
            _Env(settings_cls),
            SecretsSettingsSource(settings_cls),
        )


class AppSettings(SecretOnlyFromFile, BaseSettings):
    model_config = SettingsConfigDict(secrets_dir="/run/secrets/app", extra="ignore")
    SECRETS: ClassVar[frozenset[str]] = frozenset(
        {
            "jwt_secret",
            "dev_login_secret",
            "hmac_key",
            "prompt_canary",
            "agent_app_password",
            "redis_password",
            "gemini_api_key",
        }  # fmt: skip
    )

    # segredos (arquivos em /run/secrets/app)
    jwt_secret: str
    dev_login_secret: str
    hmac_key: str
    prompt_canary: str
    agent_app_password: str
    redis_password: str
    gemini_api_key: str | None = None  # opcional: sem ela, modo replay

    app_env: Literal["dev", "prod"] = "dev"
    limits_profile: Literal["default", "eval"] = "default"
    llm_provider: Literal["auto", "gemini", "replay", "fake"] = "auto"
    llm_backend: Literal["aistudio", "vertex"] = "aistudio"
    gcp_project: str | None = None
    gcp_location: str = "global"
    llm_model: str = "gemini-3.8-flash"
    llm_fallback_model: str = "gemini-3.5-flash-lite"
    api_port: int = 8000
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "student"
    redis_host: str = "redis"
    redis_port: int = 6379
    dev_login: bool = False
    cookie_secure: bool = False  # True atrás de HTTPS
    etl_out_dir: Path = Path("/etl-out")  # golden_login.json (caso -> person_key)
    demo_mode: bool = False  # setado por make_llm quando o LLM é replay; /me devolve ao front
    otel_capture_content: bool = False
    otel_exporter_otlp_endpoint: str | None = None  # Phoenix: http://phoenix:6006/v1/traces
    phoenix_project: str = "student-agent"  # projeto do Phoenix (export/import por rodada)
    dataset_sha: str | None = None  # sha do dataset carregado (atributo dataset.sha)
    reference_date: date = date(2026, 10, 31)
    slo: SloTargets = Field(default_factory=load_slo_targets)

    @property
    def limits(self) -> Limits:
        return self.slo.limits[self.limits_profile]

    @field_validator(
        "jwt_secret", "dev_login_secret", "hmac_key", "prompt_canary",
        "agent_app_password", "redis_password",
    )  # fmt: skip
    @classmethod
    def _secret_ok(cls, v: str) -> str:
        return check_secret(v)

    @field_validator("gemini_api_key", "otel_exporter_otlp_endpoint", "dataset_sha")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return (v or "").strip() or None

    @model_validator(mode="after")
    def _prod_hardening(self) -> Self:
        if self.app_env == "prod" and self.dev_login:
            raise ValueError("dev_login não pode ficar ligado com app_env=prod")
        if self.app_env == "prod" and not self.cookie_secure:
            raise ValueError("app_env=prod exige cookie_secure=true")
        return self

    @model_validator(mode="after")
    def _capture_only_dev_eval(self) -> Self:
        if self.otel_capture_content and not (
            self.app_env == "dev" and self.limits_profile == "eval"
        ):
            raise ValueError("otel_capture_content exige app_env=dev e limits_profile=eval")
        return self
