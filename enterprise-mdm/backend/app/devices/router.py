"""Device inventory API router.

Endpoints (guard shown as [org] permission; tenant context is always
resolved from the X-Organization-ID header — see app/devices/dependencies.py):

  POST   /devices                    [org] device.create
  GET    /devices                    [org] device.read
  GET    /devices/{device_id}        [org] device.read
  PATCH  /devices/{device_id}        [org] device.update
  DELETE /devices/{device_id}        [org] device.delete
  POST   /devices/{device_id}/assign      [org] device.assign
  POST   /devices/{device_id}/unassign    [org] device.unassign
  POST   /devices/{device_id}/block       [org] device.block
  POST   /devices/{device_id}/unblock     [org] device.unblock
  POST   /devices/{device_id}/retire      [org] device.retire
  POST   /devices/{device_id}/heartbeat   [device credential, not admin JWT]

  Enrollment approve/reject/revoke live in app/enrollment/router.py
  (mounted at this same "/devices" prefix) since they operate through
  EnrollmentService rather than DeviceService — see that module's
  docstring for the endpoint list.

  Remote device commands (lock/reboot/wipe/refresh/policy-sync, Phase 6)
  live in app/commands/router.py (also mounted at this same "/devices"
  prefix) — see that module's docstring for the endpoint list. Unlike
  `block`/`unblock` above, these actually attempt to reach the physical
  device through a PlatformManagementProvider.

`organization_id` is never accepted from the client on any request body —
see STEP 7 / docs/security/device-security.md. `imei_last4`/`wifi_mac_last4`
on responses are populated only when the caller holds device.view_sensitive
(see _device_to_response).
"""

from __future__ import annotations

import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.devices.connectivity import compute_connectivity_state
from app.devices.dependencies import get_device_service, require_device_permission
from app.devices.enums import (
    DevicePlatform,
    DeviceStatus,
    EnrollmentStatus,
    OwnershipType,
)
from app.devices.models import Device
from app.devices.schemas import (
    AssignDeviceRequest,
    CreateDeviceRequest,
    DeviceListResponse,
    DeviceResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    UpdateDeviceRequest,
)
from app.devices.service import DeviceService
from app.enrollment.dependencies import get_enrolled_device, invalid_device_credential_error
from app.jobs.dependencies import get_job_service
from app.jobs.service import JobService
from app.organizations.dependencies import OrganizationContext
from app.provider_events.models import ProviderDeviceLink
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

router = APIRouter(prefix="/devices", tags=["devices"])

MAX_PAGE_SIZE = 100


def _device_to_response(device: Device, org_context: OrganizationContext) -> DeviceResponse:
    return DeviceResponse.from_device(
        device, include_sensitive=org_context.has_permission(p.DEVICE_VIEW_SENSITIVE)
    )


def _with_provider_state(
    response: DeviceResponse, link: ProviderDeviceLink | None
) -> DeviceResponse:
    """Phase 12: overlays management_state/provider_name/provider_device_id
    onto an already-built DeviceResponse — kept separate from
    DeviceResponse.from_device (which has no DB access) so that classmethod
    stays a pure ORM mapper, reusable by app/enrollment/router.py's device
    registration response too, which has no provider link to show yet."""
    if link is None:
        return response
    return response.model_copy(
        update={
            "management_state": "MANAGED",
            "provider_name": link.provider_name,
            "provider_device_id": link.provider_device_id,
        }
    )


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def create_device(
    body: CreateDeviceRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    device = await service.create_device(
        org_context.organization_id,
        actor_user_id=current_user.user_id,
        display_name=body.display_name,
        manufacturer=body.manufacturer,
        model=body.model,
        model_identifier=body.model_identifier,
        platform=body.platform,
        operating_system=body.operating_system,
        os_version=body.os_version,
        device_type=body.device_type,
        ownership_type=body.ownership_type,
        serial_number=body.serial_number,
        device_identifier=body.device_identifier,
        imei=body.imei,
        wifi_mac=body.wifi_mac,
    )
    return _device_to_response(device, org_context)


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))
    ],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    search: str | None = Query(default=None, max_length=200),
    status_filter: DeviceStatus | None = Query(default=None, alias="status"),
    platform: DevicePlatform | None = Query(default=None),
    ownership_type: OwnershipType | None = Query(default=None),
    enrollment_status: EnrollmentStatus | None = Query(default=None),
    assigned_user_id: uuid.UUID | None = Query(default=None),
    provider_name: str | None = Query(
        default=None,
        description="Filter to devices managed by this provider (e.g. android, apple).",
    ),
    sort: str = Query(default="created_at"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    service: DeviceService = Depends(get_device_service),
    db: AsyncSession = Depends(get_db_session),
) -> DeviceListResponse:
    devices, total = await service.list_devices(
        org_context.organization_id,
        status=status_filter,
        platform=platform,
        ownership_type=ownership_type,
        enrollment_status=enrollment_status,
        assigned_user_id=assigned_user_id,
        provider_name=provider_name,
        search=search,
        sort=sort,
        sort_desc=(sort_dir == "desc"),
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    # Phase 12: one bulk lookup for the whole page, not N+1 per row — see
    # ProviderDeviceLinkRepository.get_links_for_devices.
    links = await ProviderDeviceLinkRepository(db).get_links_for_devices(
        org_context.organization_id, [d.id for d in devices]
    )
    total_pages = math.ceil(total / page_size) if total else 0
    return DeviceListResponse(
        items=[
            _with_provider_state(_device_to_response(d, org_context), links.get(d.id))
            for d in devices
        ],
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
    db: AsyncSession = Depends(get_db_session),
) -> DeviceResponse:
    device = await service.get_device(
        org_context.organization_id,
        device_id,
        actor_user_id=current_user.user_id,
        view_sensitive=org_context.has_permission(p.DEVICE_VIEW_SENSITIVE),
    )
    link = await ProviderDeviceLinkRepository(db).get_any_link_for_device(
        org_context.organization_id, device_id
    )
    return _with_provider_state(_device_to_response(device, org_context), link)


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: uuid.UUID,
    body: UpdateDeviceRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    device = await service.update_device(
        org_context.organization_id,
        device_id,
        actor_user_id=current_user.user_id,
        **body.model_dump(),
    )
    return _device_to_response(device, org_context)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_DELETE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> None:
    """Hard delete. Prefer POST /devices/{id}/retire for normal
    decommissioning — see docs/architecture/device-management.md."""
    await service.delete_device(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )


@router.post("/{device_id}/assign", response_model=DeviceResponse)
async def assign_device(
    device_id: uuid.UUID,
    body: AssignDeviceRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_ASSIGN))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    device = await service.assign_device(
        org_context.organization_id,
        device_id,
        user_id=body.user_id,
        actor_user_id=current_user.user_id,
    )
    return _device_to_response(device, org_context)


@router.post("/{device_id}/unassign", response_model=DeviceResponse)
async def unassign_device(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_UNASSIGN))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    device = await service.unassign_device(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return _device_to_response(device, org_context)


@router.post("/{device_id}/block", response_model=DeviceResponse)
async def block_device(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_BLOCK))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    """Marks the inventory record BLOCKED. Does NOT contact the physical
    device — no real Android/Apple lock/wipe command is issued in Phase 4."""
    device = await service.block_device(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return _device_to_response(device, org_context)


@router.post("/{device_id}/unblock", response_model=DeviceResponse)
async def unblock_device(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_UNBLOCK))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    device = await service.unblock_device(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return _device_to_response(device, org_context)


@router.post("/{device_id}/retire", response_model=DeviceResponse)
async def retire_device(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_RETIRE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceService = Depends(get_device_service),
) -> DeviceResponse:
    device = await service.retire_device(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return _device_to_response(device, org_context)


@router.post("/{device_id}/heartbeat", response_model=HeartbeatResponse)
async def device_heartbeat(
    device_id: uuid.UUID,
    body: HeartbeatRequest,
    device: Annotated[Device, Depends(get_enrolled_device)],
    service: DeviceService = Depends(get_device_service),
    job_service: JobService = Depends(get_job_service),
) -> HeartbeatResponse:
    """Device check-in — authenticated via X-Device-Id/X-Device-Secret
    headers (see app/enrollment/dependencies.py::get_enrolled_device), NOT
    an admin JWT. The path `device_id` must match the credential's own
    device — this is a defense-in-depth check on top of the credential
    itself; a mismatch is reported identically to any other invalid
    credential so it can't be used to enumerate device IDs."""
    if device.id != device_id:
        raise invalid_device_credential_error()

    updated = await service.record_heartbeat(
        device,
        client_version=body.client_version,
        os_version=body.os_version,
        security_patch_level=body.security_patch_level,
        is_encrypted=body.is_encrypted,
        has_screen_lock=body.has_screen_lock,
        is_rooted=body.is_rooted,
    )
    # Compliance evaluation runs asynchronously off the worker queue rather
    # than inline here — heartbeats are high-frequency and must stay fast
    # (Phase 7). enqueue_compliance_evaluation's dedup_key collapses a burst
    # of heartbeats from the same device into a single pending evaluation.
    await job_service.enqueue_compliance_evaluation(device.organization_id, device.id)
    return HeartbeatResponse(
        device_id=updated.id,
        last_seen_at=updated.last_seen_at,
        connectivity_state=compute_connectivity_state(updated.last_seen_at),
    )
