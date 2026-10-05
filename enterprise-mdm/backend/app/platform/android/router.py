"""Android provider webhook + operational endpoints (Phase 9).

  POST /webhooks/android/pubsub   [Pub/Sub push, token query param]
  GET  /webhooks/android/health   [SUPER_ADMIN]

No CSRF, no cookie/session auth on the webhook — Google Pub/Sub calls it
directly as a service, never a browser (see
app/auth/middleware.py::EXEMPT_PATHS for why that's still safe). See
app/platform/android/webhook.py for the parsing/verification logic this
router only wires up.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.database.session import get_db_session
from app.devices.enums import DevicePlatform
from app.enrollment.dependencies import get_enrollment_service
from app.platform.android.client import AndroidApiClient
from app.platform.android.webhook import (
    decode_pubsub_envelope,
    extract_message_id,
    extract_provider_device_id,
    map_notification_to_event_type,
    verify_push_token,
)
from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError
from app.platform.health import ProviderHealth, check_android_health
from app.provider_events.dependencies import get_provider_event_service
from app.provider_events.service import ProviderEventService
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, require_permission
from app.repositories.provider_device_link_repository import (
    AmbiguousProviderDeviceLinkError,
    ProviderDeviceLinkRepository,
)

router = APIRouter(prefix="/webhooks/android", tags=["android-webhook"])
logger = get_logger(__name__)


async def _try_auto_enroll_android_device(
    db: AsyncSession, settings: Settings, provider_device_id: str
) -> uuid.UUID | None:
    """Phase 11: a genuinely never-before-seen Android device just checked
    in. Fetches its full resource (only the one real network call this
    entire webhook handler ever makes) to learn `enrollmentTokenName`, then
    asks EnrollmentService to resolve that back to an organization and
    auto-enroll. Returns the organization_id on success, None on any
    failure mode (not configured, transient provider error, no matching
    token, token not active) — every failure here just means the event
    stays recorded as unresolved, exactly Phase 9's existing behavior, not
    a reason to fail the whole webhook request."""
    if not settings.android_service_account_json or not settings.android_enterprise_id:
        return None
    client = AndroidApiClient(settings.android_service_account_json, settings.android_enterprise_id)
    try:
        device_resource = await client.get_device_resource(provider_device_id)
    except (PlatformProviderUnavailableError, PlatformCommandRejectedError) as exc:
        logger.warning(
            "android_webhook_device_lookup_failed",
            provider_device_id=provider_device_id,
            error=exc.message,
        )
        return None
    reference = device_resource.get("enrollmentTokenName")
    if not isinstance(reference, str) or not reference:
        return None
    enrollment_service = get_enrollment_service(db)
    device = await enrollment_service.resolve_provider_checkin(
        provider_name="android",
        provider_device_id=provider_device_id,
        provider_enrollment_reference=reference,
        platform=DevicePlatform.ANDROID,
    )
    return device.organization_id if device is not None else None


@router.post("/pubsub")
async def android_pubsub_push(
    request: Request,
    token: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db_session),
    event_service: ProviderEventService = Depends(get_provider_event_service),
) -> dict[str, str]:
    """Returns 2xx unconditionally once the envelope is authenticated and
    parseable — Pub/Sub retries on non-2xx, and a *processing* failure
    (unrecognized event type, resolution failure) is recorded on the
    ProviderEvent row itself (processing_status=FAILED/IGNORED), not
    something redelivery could fix. Only authentication failure and a
    malformed envelope (both client-side problems Pub/Sub itself sent)
    return non-2xx."""
    settings = get_settings()
    if not verify_push_token(settings.android_pubsub_verification_token, token):
        logger.warning("android_webhook_auth_failed")
        raise AppError(
            code="WEBHOOK_UNAUTHORIZED",
            message="Invalid or missing verification token.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        envelope = await request.json()
        if not isinstance(envelope, dict):
            raise ValueError("Request body is not a JSON object.")
        payload = decode_pubsub_envelope(envelope)
    except ValueError as exc:
        raise AppError(
            code="WEBHOOK_MALFORMED_PAYLOAD",
            message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    notification_type = str(payload.get("notificationType", "UNKNOWN"))
    device_resource_name = payload.get("device")
    message_id = extract_message_id(envelope)

    organization_id = None
    provider_device_id = None
    if isinstance(device_resource_name, str) and device_resource_name:
        provider_device_id = extract_provider_device_id(device_resource_name)
        try:
            link = await ProviderDeviceLinkRepository(db).get_by_provider_device_id_unscoped(
                "android", provider_device_id
            )
        except AmbiguousProviderDeviceLinkError:
            logger.error(
                "android_webhook_ambiguous_device", provider_device_id=provider_device_id
            )
            link = None
        if link is not None:
            organization_id = link.organization_id
        else:
            # Phase 11: never seen this device before. The Pub/Sub
            # notification itself only ever carries the resource *name*
            # (see this module's own historical COMMAND-notification
            # comment above) — recovering which organization minted the
            # enrollment token this device enrolled under requires one
            # follow-up `devices.get` call for the full resource, done
            # only here (once per genuinely unknown device), never on
            # every notification for an already-linked one.
            organization_id = await _try_auto_enroll_android_device(
                db, settings, provider_device_id
            )

    event = await event_service.ingest_event(
        provider_name="android",
        provider_event_id=message_id
        or f"unkeyed:{device_resource_name or 'unknown'}:{notification_type}",
        event_type=map_notification_to_event_type(notification_type),
        payload=payload,
        organization_id=organization_id,
        provider_device_id=provider_device_id,
    )
    await db.commit()
    return {"status": "accepted", "event_id": str(event.id)}


@router.get("/health")
async def android_provider_health(
    current_user: Annotated[CurrentUser, Depends(require_permission(p.AUDIT_READ))],
) -> ProviderHealth:
    """Platform-level (not organization-scoped) — Android credentials are a
    deployment-wide setting, not per-organization, so this is gated by a
    platform permission rather than X-Organization-ID context. Reuses
    AUDIT_READ (broad operational-visibility intent) rather than minting a
    new permission for a single read-only diagnostic endpoint. Never
    returns the service-account key material itself. See also the
    unified `GET /providers/status` (Phase 10,
    app/platform/router.py), which returns this same shape for both
    platforms in one call."""
    return await check_android_health(get_settings())
