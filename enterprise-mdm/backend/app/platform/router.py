"""Unified provider-status admin API (Phase 10).

  GET /providers/status  [SUPER_ADMIN, i.e. AUDIT_READ platform permission]

Aggregates app/platform/android/router.py's and apple/router.py's
per-platform health checks (added Phase 9/10) into one call — a single
dashboard-friendly response instead of two round trips. Platform-level,
not organization-scoped: Android/Apple credentials are deployment-wide
settings (`Settings.android_*`/`apple_apns_*`), not per-organization, so
this is gated by a platform role rather than X-Organization-ID context,
exactly like the two per-platform endpoints it wraps. Never exposes
credentials, tokens, or certificate material — see
app/platform/health.py for the shared shape both this and the
per-platform endpoints return.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.platform.health import ProviderHealth, check_android_health, check_apple_health
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, require_permission

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderStatusResponse(TypedDict):
    android: ProviderHealth
    apple: ProviderHealth


@router.get("/status")
async def provider_status(
    current_user: Annotated[CurrentUser, Depends(require_permission(p.AUDIT_READ))],
) -> ProviderStatusResponse:
    settings = get_settings()
    return {
        "android": await check_android_health(settings),
        "apple": await check_apple_health(settings),
    }
