"""Centralized application configuration.

All configuration is sourced from environment variables (see
`.env.example` at the repo root). Nothing here may contain a real secret —
defaults exist only for local development convenience and are documented as
such.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded once and cached via `get_settings()`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application identity ---
    app_name: str = Field(default="EnterpriseMDM")
    app_env: Literal["local", "test", "staging", "production"] = Field(default="local")
    api_v1_prefix: str = Field(default="/api/v1")

    # --- Logging ---
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    # --- Database ---
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://mdm:mdm@localhost:5432/enterprise_mdm")
    )

    # --- Redis ---
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    # --- Auth ---
    jwt_secret: str = Field(default="CHANGE_ME_INSECURE_LOCAL_ONLY")
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=15)
    refresh_token_expire_days: int = Field(default=7)

    # Cookie security settings
    # In production these MUST be True/Strict; local dev HTTP allows False/Lax.
    cookie_secure: bool = Field(default=False)
    cookie_samesite: Literal["lax", "strict", "none"] = Field(default="lax")
    cookie_domain: str | None = Field(default=None)

    # CSRF (double-submit cookie) secret — separate from JWT secret.
    csrf_secret: str = Field(default="CHANGE_ME_CSRF_SECRET_LOCAL_ONLY")

    # --- Password policy ---
    password_min_length: int = Field(default=12)
    # NIST SP 800-63B: do NOT require specific character classes.
    # Minimum length + breach-check is the recommended approach.
    password_max_length: int = Field(default=128)

    # --- Account lockout ---
    max_login_attempts: int = Field(default=5)
    lockout_duration_minutes: int = Field(default=15)

    # --- Rate limiting ---
    # Per-IP login attempts before temporary block (separate from account lockout)
    login_rate_limit_per_ip: int = Field(default=20)
    login_rate_limit_window_seconds: int = Field(default=300)

    # --- Password reset ---
    password_reset_token_expire_minutes: int = Field(default=60)

    # --- Email verification ---
    email_verify_token_expire_hours: int = Field(default=24)
    # If True, users must verify email before logging in. False = non-blocking.
    email_verification_required: bool = Field(default=False)

    # --- Organization invitations (Phase 3) ---
    organization_invitation_expire_hours: int = Field(default=72)

    # --- Device connectivity thresholds (Phase 5) ---
    device_online_threshold_minutes: int = Field(default=5)
    device_stale_threshold_hours: int = Field(default=24)

    # --- Remote device commands (Phase 6) ---
    # "mock" (default): always MockManagementProvider, regardless of device
    # platform — safe for tests and local dev, never touches a real
    # network. "real": Android/Apple providers, which require the
    # credential settings below and are NOT configured in this
    # environment — see docs/architecture/device-commands.md.
    platform_provider_mode: Literal["mock", "real"] = Field(default="mock")
    device_command_expire_hours: int = Field(default=24)

    # Android Management API credentials — only read when
    # platform_provider_mode="real". Never committed; unset here.
    android_service_account_json: str | None = Field(default=None)
    android_enterprise_id: str | None = Field(default=None)

    # Apple MDM / APNs credentials — only read when
    # platform_provider_mode="real". Never committed; unset here.
    apple_apns_key_path: str | None = Field(default=None)
    apple_apns_key_id: str | None = Field(default=None)
    apple_apns_team_id: str | None = Field(default=None)
    apple_apns_topic: str | None = Field(default=None)

    # --- Provider event webhooks (Phase 9) ---
    # Shared verification token appended to the Android Pub/Sub push
    # subscription's endpoint URL as a query parameter (Google's own
    # documented mechanism for authenticating a push endpoint beyond bare
    # HTTPS) — see app/provider_events/webhooks/android.py. Unset means the
    # endpoint rejects every request (fails closed, never "accept
    # anything"); never committed.
    android_pubsub_verification_token: str | None = Field(default=None)

    # --- Email provider ---
    # "console" (dev, logs to stdout) | "null" (test, discards) | "smtp" (future)
    email_provider: Literal["console", "null", "smtp"] = Field(default="console")

    # --- Seed admin (used by scripts/seed_admin.py) ---
    seed_admin_email: str = Field(default="admin@enterprise-mdm.com")
    seed_admin_password: str = Field(default="CHANGE_ME_SEED_ADMIN_PASSWORD")
    seed_admin_first_name: str = Field(default="Platform")
    seed_admin_last_name: str = Field(default="Administrator")

    # --- CORS ---
    # NoDecode: pydantic-settings otherwise tries to json.loads() any
    # environment-sourced value for a list-typed field BEFORE this class's
    # own `_split_csv` validator ever runs — a plain CSV string like
    # "http://localhost:3000" (exactly what .env.example documents and
    # every real .env file actually contains) is not valid JSON, so
    # without NoDecode, constructing Settings() from a real environment
    # crashes at import time, before the application even starts. Never
    # caught by any test in this project's history because no test ever
    # set CORS_ORIGINS as a real env var (the Python default bypasses the
    # environment source's decode step entirely) — only surfaced when a
    # real .env file was loaded by a real container for the first time.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Object storage (reserved for reports, Phase 11) ---
    storage_bucket: str | None = Field(default=None)
    storage_region: str | None = Field(default=None)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()


class ProductionConfigError(RuntimeError):
    """Raised at process startup when APP_ENV=production but the resolved
    configuration is unsafe to serve traffic with. Deliberately a hard
    failure (crashes the process before it ever binds a port) rather than
    a warning — an insecure secret/cookie flag silently reaching
    production is exactly the kind of gap that stays invisible until
    exploited. See app/main.py::create_app, which calls
    validate_production_config before constructing the FastAPI app."""


# The exact insecure default values shipped in this file / .env.example —
# checked verbatim so a deploy that forgot to override one of these three
# specific settings fails loudly instead of quietly running with a secret
# this entire codebase's own source history has committed in plaintext.
_INSECURE_DEFAULTS = {
    "jwt_secret": "CHANGE_ME_INSECURE_LOCAL_ONLY",
    "csrf_secret": "CHANGE_ME_CSRF_SECRET_LOCAL_ONLY",
    "seed_admin_password": "CHANGE_ME_SEED_ADMIN_PASSWORD",
}
# NIST SP 800-63B doesn't mandate a specific length for a machine-generated
# HMAC/signing secret the way it does for a human-chosen password, but 32
# bytes (256 bits) is the standard minimum for an HS256 signing key —
# below this, the key itself becomes the weakest link regardless of
# algorithm choice.
_MIN_SECRET_LENGTH = 32


def validate_production_config(settings: Settings) -> None:
    """Fail-fast startup check — a no-op unless `app_env == "production"`.
    Every other environment (local/test/staging) keeps using the
    convenience defaults above unchecked, exactly as before this function
    existed; this only ever fires for a deployment that has explicitly set
    APP_ENV=production, which is the one environment where "silently
    serving with a placeholder secret" is unacceptable rather than merely
    inconvenient."""
    if not settings.is_production:
        return

    errors: list[str] = []

    for field_name, insecure_value in _INSECURE_DEFAULTS.items():
        if getattr(settings, field_name) == insecure_value:
            errors.append(
                f"{field_name.upper()} is still the insecure development default "
                f"— set a real value via the environment."
            )

    if len(settings.jwt_secret) < _MIN_SECRET_LENGTH:
        errors.append(f"JWT_SECRET must be at least {_MIN_SECRET_LENGTH} characters.")
    if settings.jwt_secret == settings.csrf_secret:
        errors.append(
            "JWT_SECRET and CSRF_SECRET must not be the same value "
            "(a leak of one must not compromise the other)."
        )

    if not settings.cookie_secure:
        errors.append(
            "COOKIE_SECURE must be true in production — auth cookies must require HTTPS."
        )
    if settings.cookie_samesite == "none" and not settings.cookie_secure:
        # Browsers already reject SameSite=None without Secure, but this
        # documents the requirement explicitly rather than relying on
        # every browser enforcing it identically.
        errors.append("COOKIE_SAMESITE=none requires COOKIE_SECURE=true.")

    unsafe_origins = [
        origin
        for origin in settings.cors_origins
        if origin == "*" or "localhost" in origin or "127.0.0.1" in origin
    ]
    if unsafe_origins:
        errors.append(
            f"CORS_ORIGINS contains development/wildcard origin(s) {unsafe_origins} "
            f"— set the real frontend origin(s)."
        )

    if errors:
        raise ProductionConfigError(
            "Refusing to start with APP_ENV=production and unsafe configuration:\n- "
            + "\n- ".join(errors)
        )
