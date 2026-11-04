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
        AppSettings(jwt_secret="x" * 31)
    with pytest.raises(ValidationError):
        AppSettings(jwt_secret="")
    AppSettings(jwt_secret="x" * 32)


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
    s = AppSettings(
        app_env="dev", limits_profile="eval", llm_provider="gemini", otel_capture_content=True
    )
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
    e = AppSettings(limits_profile="eval", llm_provider="gemini").limits
    assert e.global_ceiling_usd == 40 and e.degrade_at is None


def test_slo_components_and_estimates(app_secrets: Path) -> None:
    s = AppSettings()
    assert set(s.slo.components) >= {"tool_call", "answer_call", "overhead"}
    assert s.slo.admission.est_requests > 0 and s.slo.admission.est_tokens > 0


def test_default_model_has_quota_and_price(app_secrets: Path) -> None:
    from app.obs.cost import _PRICES

    s = AppSettings()
    assert s.llm_model == "gemini-3.8-flash"
    assert s.llm_model in s.slo.quotas and s.llm_model in _PRICES


PROD = {"app_env": "prod", "cookie_secure": True}


@pytest.mark.parametrize("provider", ["replay", "fake", "loadtest", "auto"])
def test_prod_rejects_scripted_or_keyless_provider(app_secrets: Path, provider: str) -> None:
    with pytest.raises(ValidationError):
        AppSettings(llm_provider=provider, **PROD)  # type: ignore[arg-type]


def test_prod_accepts_gemini_or_auto_with_key(app_secrets: Path) -> None:
    (app_secrets / "gemini_api_key").write_text("AIza-whatever\n")
    AppSettings(llm_provider="gemini", **PROD)  # type: ignore[arg-type]
    AppSettings(llm_provider="auto", **PROD)  # type: ignore[arg-type]


def test_prod_requires_default_limits(app_secrets: Path) -> None:
    (app_secrets / "gemini_api_key").write_text("AIza-whatever\n")
    for prof in ("eval", "load"):
        with pytest.raises(ValidationError):
            AppSettings(limits_profile=prof, **PROD)  # type: ignore[arg-type]
    AppSettings(**PROD)  # type: ignore[arg-type]


@pytest.mark.parametrize("provider", ["replay", "fake", "auto"])
def test_eval_profile_rejects_replay_fake_or_keyless_auto(app_secrets: Path, provider: str) -> None:
    with pytest.raises(ValidationError, match="eval"):
        AppSettings(limits_profile="eval", llm_provider=provider)  # type: ignore[arg-type]


def test_eval_profile_accepts_gemini_or_auto_with_key(app_secrets: Path) -> None:
    (app_secrets / "gemini_api_key").write_text("AIza-whatever\n")
    AppSettings(limits_profile="eval", llm_provider="gemini")
    AppSettings(limits_profile="eval", llm_provider="auto")


def _keys(app_secrets: Path, *names: str) -> None:
    for n in names:
        (app_secrets / n).write_text("key-whatever\n")


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ((), "replay"),
        (("openai_api_key",), "openai"),
        (("anthropic_api_key", "openai_api_key"), "anthropic"),
        (("gemini_api_key", "anthropic_api_key", "openai_api_key"), "gemini"),
    ],
)
def test_auto_resolves_in_order(app_secrets: Path, keys: tuple[str, ...], expected: str) -> None:
    _keys(app_secrets, *keys)
    assert AppSettings().resolved_provider == expected


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("gemini", "gemini-3.8-flash"),
        ("anthropic", "claude-opus-5-5"),
        ("openai", "gpt-6.1-terra"),
        ("replay", "gemini-3.8-flash"),
        ("fake", "gemini-3.8-flash"),
        ("loadtest", "gemini-3.8-flash"),
    ],
)
def test_default_model_per_provider(app_secrets: Path, provider: str, model: str) -> None:
    _keys(app_secrets, "gemini_api_key", "anthropic_api_key", "openai_api_key")
    s = AppSettings(llm_provider=provider)  # type: ignore[arg-type]
    assert s.llm_model == model and s.resolved_provider == provider


def test_auto_model_follows_resolved_provider(app_secrets: Path) -> None:
    _keys(app_secrets, "openai_api_key")
    assert AppSettings().llm_model == "gpt-6.1-terra"


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "gpt-6.1-terra", "gpt-6-luna"],
)
def test_new_models_have_quota_and_price(app_secrets: Path, model: str) -> None:
    from app.obs.cost import _PRICES

    assert model in AppSettings().slo.quotas and model in _PRICES


def test_model_without_quota_is_rejected(app_secrets: Path) -> None:
    with pytest.raises(ValidationError, match="cota"):
        AppSettings(llm_model="modelo-sem-cota")


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_explicit_provider_needs_its_key(app_secrets: Path, provider: str) -> None:
    with pytest.raises(ValidationError, match=f"{provider}_api_key"):
        AppSettings(llm_provider=provider)  # type: ignore[arg-type]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_prod_and_eval_accept_new_providers(app_secrets: Path, provider: str) -> None:
    _keys(app_secrets, f"{provider}_api_key")
    AppSettings(llm_provider=provider, **PROD)  # type: ignore[arg-type]
    AppSettings(llm_provider="auto", **PROD)  # type: ignore[arg-type]
    AppSettings(limits_profile="eval", llm_provider=provider)  # type: ignore[arg-type]
    AppSettings(limits_profile="eval", llm_provider="auto")


def test_conservative_model_is_the_priciest(app_secrets: Path) -> None:
    from datetime import date
    from decimal import Decimal

    from app.agent.llm import Usage
    from app.obs.cost import _PRICES, cost_usd
    from app.service.turn import CONSERVATIVE_MODEL

    on = date(2026, 11, 4)
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    costs: dict[str, Decimal] = {m: cost_usd(u, m, on) for m in _PRICES}
    assert CONSERVATIVE_MODEL == max(costs, key=lambda m: costs[m])


def test_validation_error_hides_secret_values(app_secrets: Path) -> None:
    # o erro de subida vai para o log do container: nem prefixo de segredo pode aparecer nele
    with pytest.raises(ValidationError) as e:
        AppSettings(jwt_secret="SEGREDOVAZADO" + "x" * 30, llm_provider="anthropic")
    assert "SEGREDO" not in str(e.value) and "input_value" not in str(e.value)


@pytest.mark.parametrize(
    ("provider", "model"),
    [("anthropic", "gpt-6.1-terra"), ("openai", "claude-opus-5-5"), ("gemini", "claude-opus-5-5")],
)
def test_model_must_match_provider_family(app_secrets: Path, provider: str, model: str) -> None:
    _keys(app_secrets, "gemini_api_key", "anthropic_api_key", "openai_api_key")
    with pytest.raises(ValidationError, match="família"):
        AppSettings(llm_provider=provider, llm_model=model)  # type: ignore[arg-type]


def test_auto_with_foreign_model_is_rejected(app_secrets: Path) -> None:
    _keys(app_secrets, "anthropic_api_key", "openai_api_key")
    with pytest.raises(ValidationError, match="família"):
        AppSettings(llm_model="gpt-6.1-terra")


def test_scripted_providers_skip_family_check(app_secrets: Path) -> None:
    for p in ("replay", "fake", "loadtest"):
        AppSettings(llm_provider=p, llm_model="claude-opus-5-5")  # type: ignore[arg-type]
