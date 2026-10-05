"""Phase 15 — production configuration validation
(app/core/config.py::validate_production_config).

Pure-function tests, no DB/Redis required: constructs `Settings` directly
with various overrides and asserts the fail-fast behavior. Does not
re-test anything about Settings itself (Pydantic's own validation, field
defaults) — only the new Phase 15 production-safety check layered on top.
"""

from __future__ import annotations

import pytest

from app.core.config import ProductionConfigError, Settings, validate_production_config

_SECURE_SECRET_A = "a" * 40
_SECURE_SECRET_B = "b" * 40


def _secure_production_settings(**overrides: object) -> Settings:
    """A minimal, otherwise-safe production configuration — each test
    overrides exactly the one field it's probing."""
    fields: dict[str, object] = {
        "app_env": "production",
        "jwt_secret": _SECURE_SECRET_A,
        "csrf_secret": _SECURE_SECRET_B,
        "seed_admin_password": "a-real-generated-password-not-the-default",
        "cookie_secure": True,
        "cookie_samesite": "lax",
        "cors_origins": ["https://app.example.com"],
    }
    fields.update(overrides)
    return Settings(**fields)  # type: ignore[arg-type]


def test_secure_production_config_passes() -> None:
    validate_production_config(_secure_production_settings())


def test_non_production_env_is_never_validated() -> None:
    """local/test/staging keep using convenience defaults unchecked —
    this function is a no-op unless app_env == 'production'."""
    insecure = Settings(app_env="local")
    validate_production_config(insecure)  # must not raise


@pytest.mark.parametrize(
    "field_name",
    ["jwt_secret", "csrf_secret", "seed_admin_password"],
)
def test_insecure_default_secret_rejected_in_production(field_name: str) -> None:
    defaults = {
        "jwt_secret": "CHANGE_ME_INSECURE_LOCAL_ONLY",
        "csrf_secret": "CHANGE_ME_CSRF_SECRET_LOCAL_ONLY",
        "seed_admin_password": "CHANGE_ME_SEED_ADMIN_PASSWORD",
    }
    settings = _secure_production_settings(**{field_name: defaults[field_name]})
    with pytest.raises(ProductionConfigError, match=field_name.upper()):
        validate_production_config(settings)


def test_short_jwt_secret_rejected_in_production() -> None:
    settings = _secure_production_settings(jwt_secret="short-secret")
    with pytest.raises(ProductionConfigError, match="JWT_SECRET"):
        validate_production_config(settings)


def test_jwt_and_csrf_secret_reuse_rejected_in_production() -> None:
    settings = _secure_production_settings(csrf_secret=_SECURE_SECRET_A)
    with pytest.raises(ProductionConfigError, match="JWT_SECRET and CSRF_SECRET"):
        validate_production_config(settings)


def test_insecure_cookie_rejected_in_production() -> None:
    settings = _secure_production_settings(cookie_secure=False)
    with pytest.raises(ProductionConfigError, match="COOKIE_SECURE"):
        validate_production_config(settings)


@pytest.mark.parametrize(
    "origin",
    ["*", "http://localhost:3000", "http://127.0.0.1:3000"],
)
def test_dev_or_wildcard_cors_origin_rejected_in_production(origin: str) -> None:
    settings = _secure_production_settings(cors_origins=[origin])
    with pytest.raises(ProductionConfigError, match="CORS_ORIGINS"):
        validate_production_config(settings)


def test_multiple_violations_are_all_reported_together() -> None:
    """A single failed deploy attempt should surface every problem at
    once, not just the first — an operator fixing one and redeploying to
    discover the next is exactly the friction this aggregates away."""
    settings = _secure_production_settings(
        jwt_secret="CHANGE_ME_INSECURE_LOCAL_ONLY", cookie_secure=False
    )
    with pytest.raises(ProductionConfigError) as exc_info:
        validate_production_config(settings)
    message = str(exc_info.value)
    assert "JWT_SECRET" in message
    assert "COOKIE_SECURE" in message
