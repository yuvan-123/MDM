"""Device group API router.

  GET    /device-groups                          [org] device_group.read
  POST   /device-groups                          [org] device_group.create
  GET    /device-groups/{id}                      [org] device_group.read
  PATCH  /device-groups/{id}                      [org] device_group.update
  DELETE /device-groups/{id}                      [org] device_group.delete
  GET    /device-groups/{id}/devices               [org] device_group.read
  POST   /device-groups/{id}/devices/{device_id}   [org] device_group.manage_members
  DELETE /device-groups/{id}/devices/{device_id}   [org] device_group.manage_members
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.device_groups.dependencies import get_device_group_service, require_device_group_permission
from app.device_groups.models import DeviceGroup
from app.device_groups.schemas import (
    CreateDeviceGroupRequest,
    DeviceGroupListResponse,
    DeviceGroupResponse,
    UpdateDeviceGroupRequest,
)
from app.device_groups.service import DeviceGroupService
from app.devices.schemas import DeviceResponse
from app.organizations.dependencies import OrganizationContext
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/device-groups", tags=["device-groups"])

MAX_PAGE_SIZE = 100


async def _to_response(group: DeviceGroup, service: DeviceGroupService) -> DeviceGroupResponse:
    count = await service.device_count(group.id)
    return DeviceGroupResponse(
        id=group.id,
        organization_id=group.organization_id,
        name=group.name,
        description=group.description,
        device_count=count,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.get("", response_model=DeviceGroupListResponse)
async def list_device_groups(
    org_context: Annotated[
        OrganizationContext, Depends(require_device_group_permission(p.DEVICE_GROUP_READ))
    ],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: DeviceGroupService = Depends(get_device_group_service),
) -> DeviceGroupListResponse:
    groups, total = await service.list_groups(
        org_context.organization_id, offset=offset, limit=limit
    )
    return DeviceGroupListResponse(
        items=[await _to_response(g, service) for g in groups], total=total
    )


@router.post("", response_model=DeviceGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_device_group(
    body: CreateDeviceGroupRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_group_permission(p.DEVICE_GROUP_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceGroupService = Depends(get_device_group_service),
) -> DeviceGroupResponse:
    group = await service.create_group(
        org_context.organization_id,
        actor_user_id=current_user.user_id,
        name=body.name,
        description=body.description,
    )
    return await _to_response(group, service)


@router.get("/{group_id}", response_model=DeviceGroupResponse)
async def get_device_group(
    group_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_group_permission(p.DEVICE_GROUP_READ))
    ],
    service: DeviceGroupService = Depends(get_device_group_service),
) -> DeviceGroupResponse:
    group = await service.get_group(org_context.organization_id, group_id)
    return await _to_response(group, service)


@router.patch("/{group_id}", response_model=DeviceGroupResponse)
async def update_device_group(
    group_id: uuid.UUID,
    body: UpdateDeviceGroupRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_group_permission(p.DEVICE_GROUP_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceGroupService = Depends(get_device_group_service),
) -> DeviceGroupResponse:
    group = await service.update_group(
        org_context.organization_id,
        group_id,
        actor_user_id=current_user.user_id,
        name=body.name,
        description=body.description,
    )
    return await _to_response(group, service)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device_group(
    group_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_group_permission(p.DEVICE_GROUP_DELETE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceGroupService = Depends(get_device_group_service),
) -> None:
    await service.delete_group(
        org_context.organization_id, group_id, actor_user_id=current_user.user_id
    )


@router.get("/{group_id}/devices", response_model=list[DeviceResponse])
async def list_group_devices(
    group_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_group_permission(p.DEVICE_GROUP_READ))
    ],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: DeviceGroupService = Depends(get_device_group_service),
) -> list[DeviceResponse]:
    devices, _ = await service.list_devices(
        org_context.organization_id, group_id, offset=offset, limit=limit
    )
    include_sensitive = org_context.has_permission(p.DEVICE_VIEW_SENSITIVE)
    return [DeviceResponse.from_device(d, include_sensitive=include_sensitive) for d in devices]


@router.post(
    "/{group_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def add_device_to_group(
    group_id: uuid.UUID,
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext,
        Depends(require_device_group_permission(p.DEVICE_GROUP_MANAGE_MEMBERS)),
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceGroupService = Depends(get_device_group_service),
) -> None:
    await service.add_device(
        org_context.organization_id, group_id, device_id, actor_user_id=current_user.user_id
    )


@router.delete(
    "/{group_id}/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_device_from_group(
    group_id: uuid.UUID,
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext,
        Depends(require_device_group_permission(p.DEVICE_GROUP_MANAGE_MEMBERS)),
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: DeviceGroupService = Depends(get_device_group_service),
) -> None:
    await service.remove_device(
        org_context.organization_id, group_id, device_id, actor_user_id=current_user.user_id
    )
