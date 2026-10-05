"""Provider-device-link admin API (Phase 10).

  GET    /provider-device-links           [org] provider_device_link.read
                                           (paginated, ?provider_name=&device_id=&search=)
  GET    /provider-device-links/{id}      [org] provider_device_link.read
  POST   /provider-device-links           [org] provider_device_link.create
  PATCH  /provider-device-links/{id}      [org] provider_device_link.update
  DELETE /provider-device-links/{id}      [org] provider_device_link.delete

Flat, header-scoped resource (X-Organization-ID) like jobs/provider-events.
Removes the Phase 9 operational limitation — a real admin API instead of a
direct database write. See app/provider_events/link_service.py for
validation (device ownership, uniqueness, tenant isolation) and
docs/architecture/phase-10.md.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.organizations.dependencies import OrganizationContext
from app.provider_events.dependencies import get_provider_device_link_service
from app.provider_events.dependencies import (
    require_provider_device_link_permission as require_link_permission,
)
from app.provider_events.link_schemas import (
    CreateProviderDeviceLinkRequest,
    ProviderDeviceLinkListResponse,
    ProviderDeviceLinkResponse,
    UpdateProviderDeviceLinkRequest,
)
from app.provider_events.link_service import ProviderDeviceLinkService
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/provider-device-links", tags=["provider-device-links"])

MAX_PAGE_SIZE = 100


@router.get("", response_model=ProviderDeviceLinkListResponse)
async def list_provider_device_links(
    org_context: Annotated[
        OrganizationContext, Depends(require_link_permission(p.PROVIDER_DEVICE_LINK_READ))
    ],
    provider_name: str | None = Query(default=None),
    device_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, description="Substring match on provider_device_id."),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    service: ProviderDeviceLinkService = Depends(get_provider_device_link_service),
) -> ProviderDeviceLinkListResponse:
    links, total = await service.list_links(
        org_context.organization_id,
        provider_name=provider_name,
        device_id=device_id,
        search=search,
        offset=offset,
        limit=limit,
    )
    return ProviderDeviceLinkListResponse(
        items=[ProviderDeviceLinkResponse.model_validate(link) for link in links], total=total
    )


@router.get("/{link_id}", response_model=ProviderDeviceLinkResponse)
async def get_provider_device_link(
    link_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_link_permission(p.PROVIDER_DEVICE_LINK_READ))
    ],
    service: ProviderDeviceLinkService = Depends(get_provider_device_link_service),
) -> ProviderDeviceLinkResponse:
    link = await service.get_link(org_context.organization_id, link_id)
    return ProviderDeviceLinkResponse.model_validate(link)


@router.post("", response_model=ProviderDeviceLinkResponse, status_code=status.HTTP_201_CREATED)
async def create_provider_device_link(
    body: CreateProviderDeviceLinkRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_link_permission(p.PROVIDER_DEVICE_LINK_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ProviderDeviceLinkService = Depends(get_provider_device_link_service),
) -> ProviderDeviceLinkResponse:
    link = await service.create_link(
        org_context.organization_id,
        actor_user_id=current_user.user_id,
        device_id=body.device_id,
        provider_name=body.provider_name,
        provider_device_id=body.provider_device_id,
        apple_push_token=body.apple_push_token,
    )
    return ProviderDeviceLinkResponse.model_validate(link)


@router.patch("/{link_id}", response_model=ProviderDeviceLinkResponse)
async def update_provider_device_link(
    link_id: uuid.UUID,
    body: UpdateProviderDeviceLinkRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_link_permission(p.PROVIDER_DEVICE_LINK_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ProviderDeviceLinkService = Depends(get_provider_device_link_service),
) -> ProviderDeviceLinkResponse:
    link = await service.update_link(
        org_context.organization_id,
        link_id,
        actor_user_id=current_user.user_id,
        provider_device_id=body.provider_device_id,
        apple_push_token=body.apple_push_token,
    )
    return ProviderDeviceLinkResponse.model_validate(link)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_device_link(
    link_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_link_permission(p.PROVIDER_DEVICE_LINK_DELETE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ProviderDeviceLinkService = Depends(get_provider_device_link_service),
) -> None:
    await service.delete_link(
        org_context.organization_id, link_id, actor_user_id=current_user.user_id
    )
