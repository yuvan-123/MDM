"""Apple MDM check-in / command-queue endpoints (Phase 9).

  POST /mdm/apple/{organization_id}/checkin   [Authenticate/TokenUpdate/CheckOut]
  POST /mdm/apple/{organization_id}/connect   [command poll / Status response]
  GET  /mdm/apple/health                      [SUPER_ADMIN]

No CSRF, no cookie/session auth — a real Apple device calls these
directly over its own connection, never a browser (see
app/auth/middleware.py::EXEMPT_PATH_PATTERNS). Organization-scoped by URL
path segment, the same pattern a real multi-tenant MDM server uses via a
per-organization `CheckInURL`/`ServerURL` in the enrollment profile — see
app/platform/apple/checkin.py's module docstring for the full protocol
writeup and the honest limitation on how a device gets linked at all.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.database.session import get_db_session
from app.platform.apple.checkin import (
    build_command_plist,
    empty_dequeue_plist,
    extract_push_token,
    map_status_to_event_type,
    parse_plist,
    require_udid,
)
from app.platform.health import ProviderHealth, check_apple_health
from app.provider_events.dependencies import get_provider_event_service
from app.provider_events.service import ProviderEventService
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, require_permission
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

router = APIRouter(prefix="/mdm/apple", tags=["apple-mdm"])
logger = get_logger(__name__)

_PLIST_CONTENT_TYPE = "application/x-apple-aspen-mdm"


async def _resolve_device_or_404(
    db: AsyncSession, organization_id: uuid.UUID, udid: str
) -> uuid.UUID:
    device_id = await ProviderDeviceLinkRepository(db).get_device_id(
        organization_id, "apple", udid
    )
    if device_id is None:
        logger.warning(
            "apple_checkin_unlinked_udid", organization_id=str(organization_id), udid=udid
        )
        raise AppError(
            code="APPLE_DEVICE_NOT_LINKED",
            message="This UDID is not linked to a known device in this organization — see "
            "docs/architecture/phase-9.md for why real Apple enrollment cannot populate "
            "this link in this deployment.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return device_id


@router.post("/{organization_id}/checkin")
async def apple_checkin(
    organization_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Authenticate (device announces itself), TokenUpdate (device
    registers/refreshes its APNs push token), CheckOut (device is
    unenrolling) — none of these carry a command; the correct real-protocol
    response to each is an empty 200."""
    body = await request.body()
    try:
        message = parse_plist(body)
        udid = require_udid(message)
    except ValueError as exc:
        raise AppError(
            code="APPLE_CHECKIN_MALFORMED",
            message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    message_type = message.get("MessageType")
    device_id = await _resolve_device_or_404(db, organization_id, udid)

    if message_type == "TokenUpdate":
        push_token = extract_push_token(message)
        if push_token:
            link_repo = ProviderDeviceLinkRepository(db)
            link = await link_repo.get_link_for_device(organization_id, device_id, "apple")
            if link is not None:
                # Phase 10: apple_push_token, never provider_device_id — the
                # latter holds the UDID (this row's stable resolution key)
                # and must never be overwritten by the separate, rotatable
                # push token. See ProviderDeviceLink's own docstring for the
                # Phase 9 bug this fixes.
                await link_repo.update(link, apple_push_token=push_token)
                await db.commit()
    elif message_type not in ("Authenticate", "CheckOut"):
        raise AppError(
            code="APPLE_CHECKIN_UNKNOWN_MESSAGE_TYPE",
            message=f"Unrecognized check-in MessageType: {message_type!r}.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return Response(status_code=status.HTTP_200_OK)


@router.post("/{organization_id}/connect")
async def apple_connect(
    organization_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    event_service: ProviderEventService = Depends(get_provider_event_service),
) -> Response:
    """A body containing a `Status` field is the device reporting the
    outcome of a previously-delivered command; anything else (including an
    empty body) is the device asking "what's next" — Apple's real protocol
    allows at most one outstanding command per device, so this returns
    exactly the oldest still-SENT DeviceCommand, or an empty "nothing
    queued" plist."""
    body = await request.body()
    try:
        message = parse_plist(body) if body else {}
        udid = require_udid(message) if body else None
    except ValueError as exc:
        raise AppError(
            code="APPLE_CONNECT_MALFORMED",
            message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        ) from exc

    if udid is None:
        raise AppError(
            code="APPLE_CONNECT_MALFORMED",
            message="Message is missing a string 'UDID'.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    device_id = await _resolve_device_or_404(db, organization_id, udid)

    status_value = message.get("Status")
    if isinstance(status_value, str):
        command_uuid = message.get("CommandUUID")
        event_type = map_status_to_event_type(status_value)
        if event_type is not None and isinstance(command_uuid, str) and command_uuid:
            await event_service.ingest_event(
                provider_name="apple",
                provider_event_id=f"{udid}:{command_uuid}:{status_value}",
                event_type=event_type,
                payload={"provider_command_id": command_uuid, "status": status_value},
                organization_id=organization_id,
            )
            await db.commit()
        # NotNow, or a Status with no CommandUUID (malformed/edge case):
        # acknowledge receipt but don't advance anything — the device will
        # retry, and retrying is exactly what NotNow means.
        return Response(status_code=status.HTTP_200_OK)

    command_repo = DeviceCommandRepository(db)
    pending = await command_repo.list_for_device(
        organization_id, device_id, status=DeviceCommandStatus.SENT, offset=0, limit=1
    )
    if not pending:
        return Response(content=empty_dequeue_plist(), media_type=_PLIST_CONTENT_TYPE)

    command = pending[0]
    try:
        plist_body = build_command_plist(
            str(command.provider_command_id or command.id),
            command.command_type.value,
            command.payload or {},
        )
    except ValueError:
        logger.error(
            "apple_connect_unmappable_command_type",
            command_id=str(command.id),
            command_type=command.command_type.value,
        )
        return Response(content=empty_dequeue_plist(), media_type=_PLIST_CONTENT_TYPE)
    return Response(content=plist_body, media_type=_PLIST_CONTENT_TYPE)


@router.get("/health")
async def apple_provider_health(
    current_user: Annotated[CurrentUser, Depends(require_permission(p.AUDIT_READ))],
) -> ProviderHealth:
    """Platform-level, like the Android equivalent — APNs credentials are a
    deployment-wide setting, not per-organization. See also the unified
    `GET /providers/status` (Phase 10, app/platform/router.py)."""
    return await check_apple_health(get_settings())
