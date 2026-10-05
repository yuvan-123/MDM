"""FastAPI dependencies for organization (tenant) context resolution.

Philosophy (matches app/rbac/dependencies.py): nothing here trusts
client-supplied data for authorization. No org_id JWT claim, no server-side
"active org" session state — every request re-resolves and re-validates
membership from the database, the same way get_current_user re-resolves
permissions on every request instead of trusting a stale claim.

Two entry points, sharing one resolver:
- `get_organization_context`: organization_id comes from the URL path
  (`{organization_id}`) — used by every `/organizations/{organization_id}/...`
  route, where there is no ambiguity about which org is being addressed.
- `get_current_organization`: organization_id comes from the
  `X-Organization-ID` header (or auto-defaults for single-org users) — for
  endpoints with no org id in their path. Used today by
  `/auth/select-organization`'s validation step, and is the mechanism
  future (Phase 4+) non-nested tenant-scoped endpoints (e.g. `/devices`)
  are expected to adopt.

In both cases the header/path value is only ever a *routing hint* — the
actual authorization decision always comes from re-querying
OrganizationMembership + UserRole for the authenticated user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.core.logging import get_logger
from app.database.session import get_db_session
from app.organizations.models import MembershipStatus, OrganizationStatus
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user
from app.repositories.audit_repository import AuditRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RBACRepository

ORG_HEADER = "X-Organization-ID"

logger = get_logger(__name__)


@dataclass
class OrganizationContext:
    """The authenticated principal's resolved, validated relationship to a
    specific organization for this request."""

    organization_id: uuid.UUID
    membership_id: uuid.UUID | None  # None only for a SUPER_ADMIN with no membership row
    is_platform_super_admin: bool
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has_permission(self, permission: str) -> bool:
        return self.is_platform_super_admin or permission in self.permissions


async def resolve_organization_context(
    db: AsyncSession,
    current_user: CurrentUser,
    organization_id: uuid.UUID,
) -> OrganizationContext:
    org_repo = OrganizationRepository(db)
    membership_repo = MembershipRepository(db)
    rbac_repo = RBACRepository(db)

    org = await org_repo.get_by_id(organization_id)
    if org is None:
        raise AppError(
            code="NOT_FOUND",
            message="Organization not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    is_super_admin = await rbac_repo.user_has_platform_role(
        current_user.user_id, p.SUPER_ADMIN
    )

    if is_super_admin:
        # Platform SUPER_ADMIN can address any organization regardless of
        # membership (spec: "SUPER_ADMIN can access both if permitted").
        # Still resolve a membership if one happens to exist, so a super
        # admin who is *also* a real member gets their actual org-scoped
        # permissions rather than a blanket bypass everywhere.
        membership = await membership_repo.get_by_user_and_org(
            current_user.user_id, organization_id
        )
        permissions = (
            await rbac_repo.get_org_scoped_permissions(current_user.user_id, organization_id)
            if membership
            else frozenset()
        )
        return OrganizationContext(
            organization_id=organization_id,
            membership_id=membership.id if membership else None,
            is_platform_super_admin=True,
            permissions=permissions,
        )

    if org.status != OrganizationStatus.ACTIVE:
        raise AppError(
            code="ORGANIZATION_SUSPENDED",
            message="This organization is not active.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    membership = await membership_repo.get_by_user_and_org(current_user.user_id, organization_id)
    if membership is None:
        raise AppError(
            code="ORGANIZATION_ACCESS_DENIED",
            message="You do not have access to this organization.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if membership.status != MembershipStatus.ACTIVE:
        raise AppError(
            code="MEMBERSHIP_SUSPENDED",
            message="Your membership in this organization is not active.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    permissions = await rbac_repo.get_org_scoped_permissions(
        current_user.user_id, organization_id
    )
    return OrganizationContext(
        organization_id=organization_id,
        membership_id=membership.id,
        is_platform_super_admin=False,
        permissions=permissions,
    )


async def get_organization_context(
    organization_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db_session),
) -> OrganizationContext:
    """Resolve tenant context from the `{organization_id}` path parameter.

    Use on every route under `/organizations/{organization_id}/...`.
    """
    return await resolve_organization_context(db, current_user, organization_id)


async def get_current_organization(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db_session),
    x_organization_id: str | None = Header(default=None, alias=ORG_HEADER),
) -> OrganizationContext:
    """Resolve tenant context from the `X-Organization-ID` header.

    If absent and the user has exactly one ACTIVE membership, that
    organization is used (zero-friction default for single-org users). If
    absent and ambiguous (zero or multiple memberships, or a platform
    SUPER_ADMIN with no inherent default), raises 400
    ORGANIZATION_CONTEXT_REQUIRED — the caller must specify one explicitly.
    """
    membership_repo = MembershipRepository(db)

    if x_organization_id:
        try:
            organization_id = uuid.UUID(x_organization_id)
        except ValueError as exc:
            raise AppError(
                code="INVALID_ORGANIZATION_ID",
                message="X-Organization-ID header is not a valid UUID.",
                status_code=status.HTTP_400_BAD_REQUEST,
            ) from exc
    else:
        memberships = await membership_repo.list_for_user(current_user.user_id)
        active = [m for m in memberships if m.status == MembershipStatus.ACTIVE]
        if len(active) != 1:
            raise AppError(
                code="ORGANIZATION_CONTEXT_REQUIRED",
                message="Specify X-Organization-ID — unable to determine a default organization.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        organization_id = active[0].organization_id

    return await resolve_organization_context(db, current_user, organization_id)


async def _enforce_org_permission(
    permission: str,
    org_context: OrganizationContext,
    request: Request,
    current_user: CurrentUser,
    db: AsyncSession,
) -> OrganizationContext:
    """Shared body for require_organization_permission (path-based) and
    require_current_organization_permission (header-based, Phase 4+) —
    both resolve an OrganizationContext by different means, then enforce
    the same permission check + audit-on-denial here.

    Distinct from the tenant-boundary check in `resolve_organization_context`
    (which raises ORGANIZATION_ACCESS_DENIED for non-members): this fires
    when the caller IS a validated member/super-admin but lacks the specific
    org-scoped permission, mirroring app/rbac/dependencies.py's
    require_permission/PERMISSION_DENIED for platform routes.
    """
    if not org_context.has_permission(permission):
        audit_repo = AuditRepository(db)
        await audit_repo.log_event(
            event_type=AuditEventType.PERMISSION_DENIED,
            actor_user_id=current_user.user_id,
            organization_id=org_context.organization_id,
            ip_address=(request.client.host if request.client else None),
            request_id=getattr(request.state, "request_id", None),
            metadata={
                "required_permission": permission,
                "path": request.url.path,
                "method": request.method,
            },
        )
        logger.warning(
            "organization_permission_denied",
            user_id=str(current_user.user_id),
            organization_id=str(org_context.organization_id),
            required=permission,
            path=request.url.path,
        )
        raise AppError(
            code="PERMISSION_DENIED",
            message="You do not have permission to perform this action in this organization.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return org_context


def require_organization_permission(permission: str) -> type[OrganizationContext]:
    """Return a FastAPI dependency enforcing *permission* within the
    resolved OrganizationContext (path-based — see get_organization_context).
    """

    async def _check(
        request: Request,
        org_context: Annotated[OrganizationContext, Depends(get_organization_context)],
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        db: AsyncSession = Depends(get_db_session),
    ) -> OrganizationContext:
        return await _enforce_org_permission(permission, org_context, request, current_user, db)

    return _check  # type: ignore[return-value]


def require_current_organization_permission(permission: str) -> type[OrganizationContext]:
    """Same as require_organization_permission, but resolves tenant context
    from the X-Organization-ID header via get_current_organization — for
    flat, non-nested resources with no `{organization_id}` in their path
    (devices, enrollment, device groups, policies, compliance — Phase 4+).
    """

    async def _check(
        request: Request,
        org_context: Annotated[OrganizationContext, Depends(get_current_organization)],
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        db: AsyncSession = Depends(get_db_session),
    ) -> OrganizationContext:
        return await _enforce_org_permission(permission, org_context, request, current_user, db)

    return _check  # type: ignore[return-value]
