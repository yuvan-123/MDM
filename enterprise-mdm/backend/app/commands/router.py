"""Remote device command API router.

  POST /devices/{device_id}/commands/lock            [org] device.lock
  POST /devices/{device_id}/commands/reboot          [org] device.command.reboot
  POST /devices/{device_id}/commands/wipe            [org] device.wipe (confirm=true,
                                                        idempotency_key required)
  POST /devices/{device_id}/commands/refresh         [org] device.command.refresh
  POST /devices/{device_id}/commands/policy-sync     [org] device.command.policy_sync
  POST /devices/{device_id}/commands/unenroll        [org] device.command.unenroll
                                                        (confirm=true, idempotency_key
                                                        required — Phase 12)
  GET  /devices/{device_id}/commands                 [org] device.read (paginated, ?status=)
  GET  /devices/{device_id}/commands/{command_id}    [org] device.read
  GET  /devices/{device_id}/commands/capabilities    [org] device.read (Phase 12 — which
                                                        command types this device's
                                                        provider actually supports)
  POST /devices/{device_id}/commands/{command_id}/cancel  [org] device.command.cancel

Five fixed-permission POST routes (one per verb) rather than one generic
`POST /commands` with a `command_type` body field — matches this codebase's
unbroken existing convention (Phase 4's block/unblock/retire, Phase 5's
approve/reject/revoke are all separate routes) rather than inventing
in-service permission branching.

Admin-authenticated only (cookie + CSRF via `require_device_permission`,
identical to every other Phase 4/5 mutating endpoint) — commands are
issued BY an admin, never presented BY a device, so unlike
`/devices/{id}/heartbeat` this module needs no device-credential channel
and no new CSRF exemption.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.commands.dependencies import get_command_service
from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.commands.schemas import (
    DeviceCommandCapabilitiesResponse,
    DeviceCommandListResponse,
    DeviceCommandResponse,
    IssueLockCommandRequest,
    IssuePolicySyncCommandRequest,
    IssueRebootCommandRequest,
    IssueRefreshInfoCommandRequest,
    IssueUnenrollCommandRequest,
    IssueWipeCommandRequest,
)
from app.commands.service import CommandService
from app.devices.dependencies import require_device_permission
from app.organizations.dependencies import OrganizationContext
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/devices", tags=["commands"])

MAX_PAGE_SIZE = 100


@router.post(
    "/{device_id}/commands/lock",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_lock_command(
    device_id: uuid.UUID,
    body: IssueLockCommandRequest,
    org_context: Annotated[OrganizationContext, Depends(require_device_permission(p.DEVICE_LOCK))],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    command = await service.create_command(
        org_context.organization_id,
        device_id,
        command_type=DeviceCommandType.LOCK,
        payload={"message": body.message} if body.message else None,
        idempotency_key=body.idempotency_key,
        actor_user_id=current_user.user_id,
    )
    return DeviceCommandResponse.model_validate(command)


@router.post(
    "/{device_id}/commands/reboot",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_reboot_command(
    device_id: uuid.UUID,
    body: IssueRebootCommandRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_COMMAND_REBOOT))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    command = await service.create_command(
        org_context.organization_id,
        device_id,
        command_type=DeviceCommandType.REBOOT,
        payload=None,
        idempotency_key=body.idempotency_key,
        actor_user_id=current_user.user_id,
    )
    return DeviceCommandResponse.model_validate(command)


@router.post(
    "/{device_id}/commands/wipe",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_wipe_command(
    device_id: uuid.UUID,
    body: IssueWipeCommandRequest,
    org_context: Annotated[OrganizationContext, Depends(require_device_permission(p.DEVICE_WIPE))],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    """`confirm` and `idempotency_key` are both required by
    IssueWipeCommandRequest — see docs/security/phase-6-security-review.md
    for why wipe carries stricter validation than every other command."""
    command = await service.create_command(
        org_context.organization_id,
        device_id,
        command_type=DeviceCommandType.WIPE,
        payload=None,
        idempotency_key=body.idempotency_key,
        actor_user_id=current_user.user_id,
    )
    return DeviceCommandResponse.model_validate(command)


@router.post(
    "/{device_id}/commands/refresh",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_refresh_info_command(
    device_id: uuid.UUID,
    body: IssueRefreshInfoCommandRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_COMMAND_REFRESH))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    command = await service.create_command(
        org_context.organization_id,
        device_id,
        command_type=DeviceCommandType.REFRESH_INFO,
        payload=None,
        idempotency_key=body.idempotency_key,
        actor_user_id=current_user.user_id,
    )
    return DeviceCommandResponse.model_validate(command)


@router.post(
    "/{device_id}/commands/policy-sync",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_policy_sync_command(
    device_id: uuid.UUID,
    body: IssuePolicySyncCommandRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_COMMAND_POLICY_SYNC))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    command = await service.create_command(
        org_context.organization_id,
        device_id,
        command_type=DeviceCommandType.POLICY_SYNC,
        payload=None,
        idempotency_key=body.idempotency_key,
        actor_user_id=current_user.user_id,
    )
    return DeviceCommandResponse.model_validate(command)


@router.post(
    "/{device_id}/commands/unenroll",
    response_model=DeviceCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_unenroll_command(
    device_id: uuid.UUID,
    body: IssueUnenrollCommandRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_COMMAND_UNENROLL))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    """Phase 12. `confirm` and `idempotency_key` are both required by
    IssueUnenrollCommandRequest — same safeguard shape as wipe. Only
    supported where the real provider genuinely implements device removal
    (Android's `devices.delete` today) — an unsupported platform (Apple)
    is rejected with `COMMAND_TYPE_NOT_SUPPORTED` before a command row is
    even created, see CommandService.create_command."""
    command = await service.create_command(
        org_context.organization_id,
        device_id,
        command_type=DeviceCommandType.UNENROLL,
        payload=None,
        idempotency_key=body.idempotency_key,
        actor_user_id=current_user.user_id,
    )
    return DeviceCommandResponse.model_validate(command)


@router.get("/{device_id}/commands", response_model=DeviceCommandListResponse)
async def list_device_commands(
    device_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))],
    status_filter: DeviceCommandStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandListResponse:
    commands, total = await service.list_commands(
        org_context.organization_id,
        device_id,
        status_filter=status_filter,
        offset=offset,
        limit=limit,
    )
    return DeviceCommandListResponse(
        items=[DeviceCommandResponse.model_validate(c) for c in commands], total=total
    )


@router.get(
    "/{device_id}/commands/capabilities", response_model=DeviceCommandCapabilitiesResponse
)
async def get_device_command_capabilities(
    device_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandCapabilitiesResponse:
    """Phase 12. Registered before the `/{command_id}` route below —
    otherwise FastAPI would try to parse the literal path segment
    "capabilities" as a command_id UUID and 422 instead of matching here."""
    provider_name, provider_available, supported = await service.get_supported_command_types(
        org_context.organization_id, device_id
    )
    return DeviceCommandCapabilitiesResponse(
        provider_name=provider_name,
        provider_available=provider_available,
        supported_command_types=supported,
    )


@router.get("/{device_id}/commands/{command_id}", response_model=DeviceCommandResponse)
async def get_device_command(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    command = await service.get_command(org_context.organization_id, device_id, command_id)
    return DeviceCommandResponse.model_validate(command)


@router.post(
    "/{device_id}/commands/{command_id}/cancel", response_model=DeviceCommandResponse
)
async def cancel_device_command(
    device_id: uuid.UUID,
    command_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_COMMAND_CANCEL))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: CommandService = Depends(get_command_service),
) -> DeviceCommandResponse:
    command = await service.cancel_command(
        org_context.organization_id, device_id, command_id, actor_user_id=current_user.user_id
    )
    return DeviceCommandResponse.model_validate(command)
