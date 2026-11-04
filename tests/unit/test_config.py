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


def test_limits_by_profile(app_secrets: Path) -> None:
    d = AppSettings().limits
    assert (d.input_chars, d.msgs_per_min, d.tokens_per_day, d.global_ceiling_usd) == (
        2000, 20, 200_000, 20,
    )  # fmt: skip
    assert (d.history_turns, d.history_ttl_s, d.jwt_ttl_s, d.draft_ttl_s) == (5, 1800, 1800, 600)
    assert (d.max_rounds, d.degrade_at, d.degrade_rounds) == (4, 0.8, 2)
    e = AppSettings(limits_profile="eval").limits
    assert e.global_ceiling_usd == 40 and e.degrade_at is None


def test_slo_components_and_estimates(app_secrets: Path) -> None:
    s = AppSettings()
    assert set(s.slo.components) >= {"tool_call", "answer_call", "overhead"}
    assert s.slo.admission.est_requests > 0 and s.slo.admission.est_tokens > 0
    assert AppSettings().demo_mode is False


def test_default_model_has_quota_and_price(app_secrets: Path) -> None:
    from app.obs.cost import _PRICES

    s = AppSettings()
    assert s.llm_model == "gemini-3.8-flash"
    for m in (s.llm_model, s.llm_fallback_model):
        assert m in s.slo.quotas and m in _PRICES
