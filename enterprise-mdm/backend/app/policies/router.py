"""Policy API router.

  GET    /policies                [org] policy.read
  POST   /policies                [org] policy.create
  GET    /policies/{id}            [org] policy.read
  PATCH  /policies/{id}            [org] policy.update
  DELETE /policies/{id}            [org] policy.delete (archives)
  GET    /policies/{id}/versions   [org] policy.read
  POST   /policies/{id}/assign     [org] policy.assign
  POST   /policies/{id}/unassign   [org] policy.unassign
  GET    /policies/{id}/assignments [org] policy.read
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.organizations.dependencies import OrganizationContext
from app.policies.dependencies import get_policy_service, require_policy_permission
from app.policies.models import Policy, PolicyStatus
from app.policies.schemas import (
    AssignPolicyRequest,
    CreatePolicyRequest,
    PolicyAssignmentResponse,
    PolicyListResponse,
    PolicyResponse,
    PolicyVersionResponse,
    UpdatePolicyRequest,
)
from app.policies.service import PolicyService
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/policies", tags=["policies"])

MAX_PAGE_SIZE = 100


def _to_response(policy: Policy) -> PolicyResponse:
    return PolicyResponse(
        id=policy.id,
        organization_id=policy.organization_id,
        name=policy.name,
        description=policy.description,
        status=policy.status,
        current_version=(
            PolicyVersionResponse.model_validate(policy.current_version)
            if policy.current_version
            else None
        ),
        created_by=policy.created_by,
        updated_by=policy.updated_by,
        created_at=policy.created_at,
        updated_at=policy.updated_at,
    )


@router.get("", response_model=PolicyListResponse)
async def list_policies(
    org_context: Annotated[OrganizationContext, Depends(require_policy_permission(p.POLICY_READ))],
    status_filter: PolicyStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: PolicyService = Depends(get_policy_service),
) -> PolicyListResponse:
    policies, total = await service.list_policies(
        org_context.organization_id, policy_status=status_filter, offset=offset, limit=limit
    )
    return PolicyListResponse(items=[_to_response(pol) for pol in policies], total=total)


@router.post("", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_policy(
    body: CreatePolicyRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_policy_permission(p.POLICY_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: PolicyService = Depends(get_policy_service),
) -> PolicyResponse:
    policy = await service.create_policy(
        org_context.organization_id,
        actor_user_id=current_user.user_id,
        name=body.name,
        description=body.description,
        configuration=body.configuration.model_dump(exclude_none=True),
    )
    return _to_response(policy)


@router.get("/{policy_id}", response_model=PolicyResponse)
async def get_policy(
    policy_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_policy_permission(p.POLICY_READ))],
    service: PolicyService = Depends(get_policy_service),
) -> PolicyResponse:
    policy = await service.get_policy(org_context.organization_id, policy_id)
    return _to_response(policy)


@router.patch("/{policy_id}", response_model=PolicyResponse)
async def update_policy(
    policy_id: uuid.UUID,
    body: UpdatePolicyRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_policy_permission(p.POLICY_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: PolicyService = Depends(get_policy_service),
) -> PolicyResponse:
    policy = await service.update_policy(
        org_context.organization_id,
        policy_id,
        actor_user_id=current_user.user_id,
        name=body.name,
        description=body.description,
        new_status=body.status,
        configuration=(
            body.configuration.model_dump(exclude_none=True)
            if body.configuration is not None
            else None
        ),
    )
    return _to_response(policy)


@router.delete("/{policy_id}", response_model=PolicyResponse)
async def delete_policy(
    policy_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_policy_permission(p.POLICY_DELETE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: PolicyService = Depends(get_policy_service),
) -> PolicyResponse:
    policy = await service.delete_policy(
        org_context.organization_id, policy_id, actor_user_id=current_user.user_id
    )
    return _to_response(policy)


@router.get("/{policy_id}/versions", response_model=list[PolicyVersionResponse])
async def list_policy_versions(
    policy_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_policy_permission(p.POLICY_READ))],
    service: PolicyService = Depends(get_policy_service),
) -> list[PolicyVersionResponse]:
    await service.get_policy(org_context.organization_id, policy_id)  # 404 if missing/foreign
    versions = await service.list_versions(policy_id)
    return [PolicyVersionResponse.model_validate(v) for v in versions]


@router.post("/{policy_id}/assign", response_model=PolicyAssignmentResponse)
async def assign_policy(
    policy_id: uuid.UUID,
    body: AssignPolicyRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_policy_permission(p.POLICY_ASSIGN))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: PolicyService = Depends(get_policy_service),
) -> PolicyAssignmentResponse:
    assignment = await service.assign_policy(
        org_context.organization_id,
        policy_id,
        actor_user_id=current_user.user_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )
    return PolicyAssignmentResponse.model_validate(assignment)


@router.post("/{policy_id}/unassign", status_code=status.HTTP_204_NO_CONTENT)
async def unassign_policy(
    policy_id: uuid.UUID,
    body: AssignPolicyRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_policy_permission(p.POLICY_UNASSIGN))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: PolicyService = Depends(get_policy_service),
) -> None:
    await service.unassign_policy(
        org_context.organization_id,
        policy_id,
        actor_user_id=current_user.user_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )


@router.get("/{policy_id}/assignments", response_model=list[PolicyAssignmentResponse])
async def list_policy_assignments(
    policy_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_policy_permission(p.POLICY_READ))],
    service: PolicyService = Depends(get_policy_service),
) -> list[PolicyAssignmentResponse]:
    assignments = await service.list_assignments(org_context.organization_id, policy_id)
    return [PolicyAssignmentResponse.model_validate(a) for a in assignments]
