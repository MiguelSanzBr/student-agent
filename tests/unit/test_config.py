import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AppSettings
from core.clock import reference_today
from etl.config import EtlSettings


def test_defaults_and_slo_targets_loaded(app_secrets: Path) -> None:
    s = AppSettings()
    assert s.slo.admission.quota_fraction == 0.9
    assert s.slo.infra_overhead_p95_ms == 300
    assert reference_today(s) == date(2026, 10, 31)
    assert s.llm_provider == "auto"
    assert s.otel_capture_content is False


def test_placeholder_secret_rejected(app_secrets: Path) -> None:
    with pytest.raises(ValidationError):
        AppSettings(jwt_secret="change-me" + "x" * 30)
    with pytest.raises(ValidationError):
        AppSettings(jwt_secret="my-Example-secret-" + "x" * 30)


def test_short_secret_rejected(app_secrets: Path) -> None:
    with pytest.raises(ValidationError):
        AppSettings(jwt_secret="x" * 23)
    with pytest.raises(ValidationError):
        AppSettings(jwt_secret="")
    AppSettings(jwt_secret="x" * 24)


def test_canary_32_hex(app_secrets: Path) -> None:
    assert re.fullmatch(r"[0-9a-f]{32}", AppSettings().prompt_canary)


def test_env_cannot_override_secret(app_secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    assert AppSettings().jwt_secret == "j" * 40


def test_non_secret_comes_from_env(app_secrets: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_PORT", "8011")
    assert AppSettings().api_port == 8011


def test_missing_secret_fails(app_secrets: Path) -> None:
    (app_secrets / "hmac_key").unlink()
    with pytest.raises(ValidationError):
        AppSettings()


def test_gemini_key_optional(app_secrets: Path) -> None:
    assert AppSettings().gemini_api_key is None
    (app_secrets / "gemini_api_key").write_text("AIza-whatever\n")
    assert AppSettings().gemini_api_key == "AIza-whatever"


def test_capture_content_requires_dev_eval(app_secrets: Path) -> None:
    with pytest.raises(ValidationError):
        AppSettings(app_env="prod", otel_capture_content=True)
    with pytest.raises(ValidationError):
        AppSettings(app_env="dev", limits_profile="default", otel_capture_content=True)
    s = AppSettings(app_env="dev", limits_profile="eval", otel_capture_content=True)
    assert s.otel_capture_content


def test_app_settings_has_no_etl_secrets() -> None:
    assert "etl_owner_password" not in AppSettings.model_fields
    assert "ops_audit_password" not in AppSettings.model_fields
    assert "postgres_password" not in AppSettings.model_fields


def test_etl_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "etl_owner_password").write_text("e" * 40)
    (tmp_path / "hmac_key").write_text("h" * 48)
    monkeypatch.setitem(EtlSettings.model_config, "secrets_dir", str(tmp_path))
    monkeypatch.setenv("ETL_OWNER_PASSWORD", "nope" * 10)
    assert EtlSettings().etl_owner_password == "e" * 40
    assert "jwt_secret" not in EtlSettings.model_fields
