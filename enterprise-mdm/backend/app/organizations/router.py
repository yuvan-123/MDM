"""Organization, membership, and invitation API router.

Endpoints (guard shown as [scope] permission):
  POST   /organizations                               [platform] organization.create
  GET    /organizations                               own orgs; all orgs for SUPER_ADMIN
  GET    /organizations/{organization_id}              [org] organization.read
  PATCH  /organizations/{organization_id}              [org] organization.update
  PATCH  /organizations/{organization_id}/status        [platform] organization.update
  GET    /organizations/{organization_id}/members       [org] organization.members.read
  POST   /organizations/{organization_id}/members       [org] organization.members.create
  PATCH  /organizations/{organization_id}/members/{membership_id}
      [org] organization.members.update
  DELETE /organizations/{organization_id}/members/{membership_id}
      [org] organization.members.remove
  POST   /organizations/{organization_id}/members/{membership_id}/roles
      [org] organization.members.update
  DELETE /organizations/{organization_id}/members/{membership_id}/roles/{role_id}
      [org] organization.members.update
  POST   /organizations/invitations/accept              unauthenticated, token-gated

Suspend/disable is intentionally platform-only (SUPER_ADMIN), not delegated
to org-scoped organization.update — an organization cannot suspend itself.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.organizations.dependencies import (
    OrganizationContext,
    require_organization_permission,
)
from app.organizations.membership_service import MembershipService
from app.organizations.models import OrganizationMembership
from app.organizations.schemas import (
    AcceptInvitationRequest,
    AssignOrgRoleRequest,
    CreateOrganizationRequest,
    InvitationResponse,
    InviteMemberRequest,
    MembershipResponse,
    MembershipRoleResponse,
    OrganizationResponse,
    UpdateMembershipStatusRequest,
    UpdateOrganizationRequest,
    UpdateOrganizationStatusRequest,
)
from app.organizations.service import OrganizationService
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user, require_permission
from app.rbac.service import RBACService
from app.repositories.audit_repository import AuditRepository
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.user_repository import UserRepository

router = APIRouter(prefix="/organizations", tags=["organizations"])


def _get_org_service(db: AsyncSession = Depends(get_db_session)) -> OrganizationService:
    return OrganizationService(
        org_repo=OrganizationRepository(db),
        membership_repo=MembershipRepository(db),
        audit_repo=AuditRepository(db),
    )


def _get_membership_service(db: AsyncSession = Depends(get_db_session)) -> MembershipService:
    rbac_repo = RBACRepository(db)
    return MembershipService(
        membership_repo=MembershipRepository(db),
        invitation_repo=InvitationRepository(db),
        org_repo=OrganizationRepository(db),
        user_repo=UserRepository(db),
        rbac_repo=rbac_repo,
        rbac_service=RBACService(
            rbac_repo=rbac_repo,
            user_repo=UserRepository(db),
            audit_repo=AuditRepository(db),
        ),
        audit_repo=AuditRepository(db),
    )


def _get_rbac_repo(db: AsyncSession = Depends(get_db_session)) -> RBACRepository:
    return RBACRepository(db)


async def _membership_to_response(
    membership: OrganizationMembership, rbac_repo: RBACRepository
) -> MembershipResponse:
    org_roles = await rbac_repo.get_org_scoped_roles(
        membership.user_id, membership.organization_id
    )
    return MembershipResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        user_email=membership.user.email,
        user_full_name=membership.user.full_name,
        status=membership.status,
        created_at=membership.created_at,
        roles=[
            MembershipRoleResponse(
                id=ur.id,
                role_name=ur.role.name if ur.role else "UNKNOWN",
                granted_at=ur.granted_at,
            )
            for ur in org_roles
        ],
    )


# ---------------------------------------------------------------------------
# Organization CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: CreateOrganizationRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.ORG_CREATE))],
    service: OrganizationService = Depends(_get_org_service),
) -> OrganizationResponse:
    """Create a new organization. Platform organization.create (SUPER_ADMIN) only."""
    org = await service.create_organization(name=body.name, actor_user_id=current_user.user_id)
    return OrganizationResponse.model_validate(org)


@router.get("", response_model=list[OrganizationResponse])
async def list_organizations(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    service: OrganizationService = Depends(_get_org_service),
    rbac_repo: RBACRepository = Depends(_get_rbac_repo),
) -> list[OrganizationResponse]:
    """List organizations. SUPER_ADMIN sees every organization; everyone
    else sees only organizations they are a member of."""
    is_super_admin = await rbac_repo.user_has_platform_role(current_user.user_id, p.SUPER_ADMIN)
    if is_super_admin:
        orgs, _ = await service.list_organizations(offset=offset, limit=limit)
    else:
        orgs = await service.list_for_user(current_user.user_id)
    return [OrganizationResponse.model_validate(o) for o in orgs]


@router.get("/{organization_id}", response_model=OrganizationResponse)
async def get_organization(
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_READ))
    ],
    service: OrganizationService = Depends(_get_org_service),
) -> OrganizationResponse:
    org = await service.get_by_id(org_context.organization_id)
    return OrganizationResponse.model_validate(org)


@router.patch("/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    body: UpdateOrganizationRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: OrganizationService = Depends(_get_org_service),
) -> OrganizationResponse:
    org = await service.update_organization(
        organization_id=org_context.organization_id,
        name=body.name,
        actor_user_id=current_user.user_id,
    )
    return OrganizationResponse.model_validate(org)


@router.patch("/{organization_id}/status", response_model=OrganizationResponse)
async def update_organization_status(
    organization_id: uuid.UUID,
    body: UpdateOrganizationStatusRequest,
    current_user: Annotated[CurrentUser, Depends(require_permission(p.ORG_UPDATE))],
    service: OrganizationService = Depends(_get_org_service),
) -> OrganizationResponse:
    """Suspend/disable/reactivate an organization. Platform-only (SUPER_ADMIN)
    — an organization cannot suspend itself via its own org-scoped admin."""
    org = await service.update_status(
        organization_id=organization_id,
        new_status=body.status,
        actor_user_id=current_user.user_id,
    )
    return OrganizationResponse.model_validate(org)


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


@router.get("/{organization_id}/members", response_model=list[MembershipResponse])
async def list_members(
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_MEMBERS_READ))
    ],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    membership_service: MembershipService = Depends(_get_membership_service),
    rbac_repo: RBACRepository = Depends(_get_rbac_repo),
) -> list[MembershipResponse]:
    members, _ = await membership_service.list_members(
        org_context.organization_id, offset=offset, limit=limit
    )
    return [await _membership_to_response(m, rbac_repo) for m in members]


@router.post(
    "/{organization_id}/members",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    request: Request,
    body: InviteMemberRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_MEMBERS_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    membership_service: MembershipService = Depends(_get_membership_service),
) -> InvitationResponse:
    invitation = await membership_service.invite_member(
        organization_id=org_context.organization_id,
        email=body.email,
        role_name=body.role_name,
        actor_user_id=current_user.user_id,
        base_url=str(request.base_url).rstrip("/"),
    )
    return InvitationResponse.model_validate(invitation)


@router.patch(
    "/{organization_id}/members/{membership_id}", response_model=MembershipResponse
)
async def update_member_status(
    membership_id: uuid.UUID,
    body: UpdateMembershipStatusRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_MEMBERS_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    membership_service: MembershipService = Depends(_get_membership_service),
    rbac_repo: RBACRepository = Depends(_get_rbac_repo),
) -> MembershipResponse:
    membership = await membership_service.update_member_status(
        organization_id=org_context.organization_id,
        membership_id=membership_id,
        new_status=body.status,
        actor_user_id=current_user.user_id,
    )
    return await _membership_to_response(membership, rbac_repo)


@router.delete(
    "/{organization_id}/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    membership_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_MEMBERS_REMOVE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    membership_service: MembershipService = Depends(_get_membership_service),
) -> None:
    await membership_service.remove_member(
        organization_id=org_context.organization_id,
        membership_id=membership_id,
        actor_user_id=current_user.user_id,
    )


@router.post(
    "/{organization_id}/members/{membership_id}/roles",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_member_role(
    membership_id: uuid.UUID,
    body: AssignOrgRoleRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_MEMBERS_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    membership_service: MembershipService = Depends(_get_membership_service),
    rbac_repo: RBACRepository = Depends(_get_rbac_repo),
) -> MembershipResponse:
    await membership_service.assign_org_role(
        organization_id=org_context.organization_id,
        membership_id=membership_id,
        role_name=body.role_name,
        actor_user_id=current_user.user_id,
        actor_is_platform_super_admin=org_context.is_platform_super_admin,
    )
    membership = await membership_service.get_membership(
        org_context.organization_id, membership_id
    )
    return await _membership_to_response(membership, rbac_repo)


@router.delete(
    "/{organization_id}/members/{membership_id}/roles/{user_role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member_role(
    membership_id: uuid.UUID,
    user_role_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_organization_permission(p.ORG_MEMBERS_UPDATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    membership_service: MembershipService = Depends(_get_membership_service),
) -> None:
    await membership_service.remove_org_role(
        organization_id=org_context.organization_id,
        membership_id=membership_id,
        user_role_id=user_role_id,
        actor_user_id=current_user.user_id,
        actor_is_platform_super_admin=org_context.is_platform_super_admin,
    )


# ---------------------------------------------------------------------------
# Invitations (unauthenticated — the invitation token itself is the proof of
# authorization, same trust model as password-reset tokens)
# ---------------------------------------------------------------------------


@router.post("/invitations/accept", response_model=MembershipResponse)
async def accept_invitation(
    body: AcceptInvitationRequest,
    membership_service: MembershipService = Depends(_get_membership_service),
    rbac_repo: RBACRepository = Depends(_get_rbac_repo),
) -> MembershipResponse:
    membership = await membership_service.accept_invitation(
        raw_token=body.token,
        password=body.password,
        first_name=body.first_name,
        last_name=body.last_name,
    )
    # accept_invitation doesn't eager-load .user; reload with it populated.
    full_membership = await membership_service.get_membership(
        membership.organization_id, membership.id
    )
    return await _membership_to_response(full_membership, rbac_repo)
