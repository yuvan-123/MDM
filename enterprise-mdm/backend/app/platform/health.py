"""Provider health-check helpers (Phase 9 per-platform, Phase 10 unified).

Shared by app/platform/android/router.py, app/platform/apple/router.py,
and app/platform/router.py (the new unified endpoint) so the
configured/ok/error shape and the "never expose credentials" rule live in
exactly one place. A health check never touches a real device — it only
attempts to mint (or reuse a cached) auth token, the cheapest real signal
that credentials are valid and the provider's own auth endpoint is
reachable (Phase 10 §10: never wipe/lock/send-commands/enroll/modify state
from a health check).
"""

from __future__ import annotations

from typing import TypedDict

from app.core.config import Settings
from app.platform.android.client import AndroidApiClient
from app.platform.apple.client import ApnsApiClient


class ProviderHealth(TypedDict):
    configured: bool
    ok: bool
    error: str | None


async def check_android_health(settings: Settings) -> ProviderHealth:
    if not settings.android_service_account_json or not settings.android_enterprise_id:
        return {"configured": False, "ok": False, "error": None}
    client = AndroidApiClient(settings.android_service_account_json, settings.android_enterprise_id)
    ok, error = await client.health_check()
    return {"configured": True, "ok": ok, "error": error}


async def check_apple_health(settings: Settings) -> ProviderHealth:
    if not (
        settings.apple_apns_key_path
        and settings.apple_apns_key_id
        and settings.apple_apns_team_id
        and settings.apple_apns_topic
    ):
        return {"configured": False, "ok": False, "error": None}
    client = ApnsApiClient(
        settings.apple_apns_key_path,
        settings.apple_apns_key_id,
        settings.apple_apns_team_id,
        settings.apple_apns_topic,
    )
    ok, error = await client.health_check()
    return {"configured": True, "ok": ok, "error": error}
