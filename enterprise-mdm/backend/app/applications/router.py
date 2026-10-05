"""Application management API router (Phase 14).

  GET    /applications                      [org] application.read
  POST   /applications                      [org] application.create
  GET    /applications/{id}                 [org] application.read
  PATCH  /applications/{id}                 [org] application.update
  DELETE /applications/{id}                 [org] application.delete
  POST   /applications/{id}/assign          [org] application.assign
  POST   /applications/{id}/unassign        [org] application.assign
  GET    /applications/{id}/assignments     [org] application.read
  POST   /applications/{id}/deploy          [org] application.deploy
  GET    /applications/{id}/deployments     [org] application.read
  POST   /applications/{id}/devices/{device_id}/remove
                                             [org] application.deploy
  GET    /devices/{device_id}/applications  [org] device.read

`PATCH` (not `PUT`) for update, matching this codebase's unbroken existing
convention (see app/policies/router.py) — a full-replace PUT was never
adopted anywhere else in this API.

The last route lives on the `/devices` prefix (a second router, exactly
like app/commands/router.py already shares that prefix with
app/devices/router.py) rather than nesting under `/applications`, since a
device's own application inventory is naturally read from the device's
own page.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.applications.dependencies import get_application_service, require_application_permission
from app.applications.models import Application, ApplicationDeploymentStatus
from app.applications.schemas import (
    ApplicationAssignmentResponse,
    ApplicationDeploymentListResponse,
    ApplicationDeploymentResponse,
    ApplicationListResponse,
    ApplicationResponse,
    AssignApplicationRequest,
    CreateApplicationRequest,
    DeployApplicationRequest,
    DeviceApplicationsResponse,
    DeviceApplicationView,
    UpdateApplicationRequest,
)
from app.applications.service import ApplicationService
from app.devices.dependencies import require_device_permission
from app.devices.enums import DevicePlatform
from app.organizations.dependencies import OrganizationContext
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/applications", tags=["applications"])
device_applications_router = APIRouter(prefix="/devices", tags=["applications"])

MAX_PAGE_SIZE = 100


def _to_response(application: Application) -> ApplicationResponse:
    return ApplicationResponse.model_validate(application)


@router.get("", response_model=ApplicationListResponse)
async def list_applications(
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_READ))
    ],
    platform: DevicePlatform | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationListResponse:
    items, total = await service.list_applications(
        org_context.organization_id, platform=platform, offset=offset, limit=limit
    )
    return ApplicationListResponse(items=[_to_response(a) for a in items], total=total)


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    body: CreateApplicationRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationResponse:
    application = await service.create_application(
        org_context.organization_id,
        actor_user_id=current_user.user_id,
        name=body.name,
        platform=body.platform,
        package_name=body.package_name,
        bundle_identifier=body.bundle_identifier,
        version=body.version,
        description=body.description,
        publisher=body.publisher,
        managed=body.managed,
    )
    return _to_response(application)


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application(
    application_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_READ))
    ],
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationResponse:
    application = await service.get_application(org_context.organization_id, application_id)
    return _to_response(application)


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: uuid.UUID,
    body: UpdateApplicationRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationResponse:
    application = await service.update_application(
        org_context.organization_id,
        application_id,
        actor_user_id=current_user.user_id,
        name=body.name,
        version=body.version,
        description=body.description,
        publisher=body.publisher,
        managed=body.managed,
    )
    return _to_response(application)


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_application(
    application_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_DELETE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> None:
    await service.delete_application(
        org_context.organization_id, application_id, actor_user_id=current_user.user_id
    )


@router.post("/{application_id}/assign", response_model=ApplicationAssignmentResponse)
async def assign_application(
    application_id: uuid.UUID,
    body: AssignApplicationRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_ASSIGN))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationAssignmentResponse:
    assignment = await service.assign_application(
        org_context.organization_id,
        application_id,
        actor_user_id=current_user.user_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        requirement=body.requirement,
    )
    return ApplicationAssignmentResponse.model_validate(assignment)


@router.post("/{application_id}/unassign", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_application(
    application_id: uuid.UUID,
    body: AssignApplicationRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_ASSIGN))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> None:
    await service.unassign_application(
        org_context.organization_id,
        application_id,
        actor_user_id=current_user.user_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )


@router.get("/{application_id}/assignments", response_model=list[ApplicationAssignmentResponse])
async def list_application_assignments(
    application_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_READ))
    ],
    service: ApplicationService = Depends(get_application_service),
) -> list[ApplicationAssignmentResponse]:
    assignments = await service.list_assignments(org_context.organization_id, application_id)
    return [ApplicationAssignmentResponse.model_validate(a) for a in assignments]


@router.post("/{application_id}/deploy", response_model=ApplicationDeploymentListResponse)
async def deploy_application(
    application_id: uuid.UUID,
    body: DeployApplicationRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_DEPLOY))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationDeploymentListResponse:
    deployments = await service.deploy(
        org_context.organization_id,
        application_id,
        actor_user_id=current_user.user_id,
        device_id=body.device_id,
        device_ids=body.device_ids,
        device_group_id=body.device_group_id,
        organization_wide=body.organization_wide,
    )
    return ApplicationDeploymentListResponse(
        items=[ApplicationDeploymentResponse.model_validate(d) for d in deployments],
        total=len(deployments),
    )


@router.get("/{application_id}/deployments", response_model=ApplicationDeploymentListResponse)
async def list_application_deployments(
    application_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_READ))
    ],
    device_id: uuid.UUID | None = Query(default=None),
    status_filter: ApplicationDeploymentStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationDeploymentListResponse:
    items, total = await service.list_deployments(
        org_context.organization_id,
        application_id,
        device_id=device_id,
        deployment_status=status_filter,
        offset=offset,
        limit=limit,
    )
    return ApplicationDeploymentListResponse(
        items=[ApplicationDeploymentResponse.model_validate(d) for d in items], total=total
    )


@router.post(
    "/{application_id}/devices/{device_id}/remove", response_model=ApplicationDeploymentResponse
)
async def remove_application_from_device(
    application_id: uuid.UUID,
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_application_permission(p.APP_DEPLOY))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ApplicationService = Depends(get_application_service),
) -> ApplicationDeploymentResponse:
    application = await service.get_application(org_context.organization_id, application_id)
    device = await service.get_device_or_404(org_context.organization_id, device_id)
    deployment = await service.remove_from_device(
        org_context.organization_id, application, device, actor_user_id=current_user.user_id
    )
    return ApplicationDeploymentResponse.model_validate(deployment)


@device_applications_router.get(
    "/{device_id}/applications", response_model=DeviceApplicationsResponse
)
async def get_device_applications(
    device_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))],
    service: ApplicationService = Depends(get_application_service),
) -> DeviceApplicationsResponse:
    device = await service.get_device_or_404(org_context.organization_id, device_id)
    rows = await service.get_device_applications(org_context.organization_id, device)
    return DeviceApplicationsResponse(
        items=[
            DeviceApplicationView(
                application=_to_response(application),
                requirement=requirement,
                deployment=(
                    ApplicationDeploymentResponse.model_validate(deployment)
                    if deployment is not None
                    else None
                ),
            )
            for application, requirement, deployment in rows
        ]
    )
