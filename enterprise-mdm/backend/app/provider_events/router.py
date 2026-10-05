"""Provider-event admin API (Phase 8 read, Phase 10 retry).

  GET  /provider-events                    [org] provider_event.read
                                            (paginated, ?provider_name=&event_type=&status=)
  GET  /provider-events/{event_id}         [org] provider_event.read
  POST /provider-events/{event_id}/retry   [org] provider_event.retry

Flat, header-scoped resource (X-Organization-ID) like jobs/devices/policies.
Only ever returns events with a resolved `organization_id` matching the
caller's own — an unresolved (organization_id=NULL) event is invisible
through this endpoint by construction (ProviderEventRepository.get_for_organization/
list_for_organization both filter on it), not merely by convention. See
docs/architecture/provider-events.md §5 for why that's an accepted
boundary rather than an oversight: there is no platform-wide admin surface
in this codebase to route an orphaned event to.

`payload` is returned as-is, not re-sanitized at this layer — the same
"must never contain credentials" invariant this codebase already relies on
for DeviceCommand.result/payload is enforced at ingestion time
(app/provider_events/models.py), not re-checked on every read.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.organizations.dependencies import OrganizationContext
from app.provider_events.dependencies import (
    get_provider_event_service,
    require_provider_event_permission,
)
from app.provider_events.models import ProviderEventProcessingStatus
from app.provider_events.schemas import ProviderEventListResponse, ProviderEventResponse
from app.provider_events.service import ProviderEventService
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/provider-events", tags=["provider-events"])

MAX_PAGE_SIZE = 100


@router.get("", response_model=ProviderEventListResponse)
async def list_provider_events(
    org_context: Annotated[
        OrganizationContext, Depends(require_provider_event_permission(p.PROVIDER_EVENT_READ))
    ],
    provider_name: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    processing_status: ProviderEventProcessingStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    service: ProviderEventService = Depends(get_provider_event_service),
) -> ProviderEventListResponse:
    events, total = await service.list_events(
        org_context.organization_id,
        provider_name=provider_name,
        event_type=event_type,
        processing_status=processing_status,
        offset=offset,
        limit=limit,
    )
    return ProviderEventListResponse(
        items=[ProviderEventResponse.model_validate(e) for e in events], total=total
    )


@router.get("/{event_id}", response_model=ProviderEventResponse)
async def get_provider_event(
    event_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_provider_event_permission(p.PROVIDER_EVENT_READ))
    ],
    service: ProviderEventService = Depends(get_provider_event_service),
) -> ProviderEventResponse:
    event = await service.get_event(org_context.organization_id, event_id)
    return ProviderEventResponse.model_validate(event)


@router.post("/{event_id}/retry", response_model=ProviderEventResponse)
async def retry_provider_event(
    event_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_provider_event_permission(p.PROVIDER_EVENT_RETRY))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ProviderEventService = Depends(get_provider_event_service),
) -> ProviderEventResponse:
    event = await service.retry_event(
        org_context.organization_id, event_id, actor_user_id=current_user.user_id
    )
    return ProviderEventResponse.model_validate(event)
