"""Organization membership service — invitations, membership lifecycle,
and org-scoped role assignment.

Role grant/revoke is delegated to the existing RBACService.assign_role /
remove_role (organization_id is already a first-class parameter there —
see docs/security/rbac.md and the Phase 3 design notes in
docs/architecture/multi-tenancy.md for why a parallel role table was not
introduced).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status

from app.audit.models import AuditEventType
from app.auth.email import get_email_service
from app.auth.password import check_password_policy, hash_password
from app.auth.tokens import generate_otp_token, hash_otp_token
from app.core.config import get_settings
from app.core.errors import AppError
from app.organizations.models import (
    MembershipStatus,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationStatus,
)
from app.rbac import permissions as p
from app.rbac.models import UserRole
from app.rbac.service import RBACService
from app.repositories.audit_repository import AuditRepository
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.user_repository import UserRepository
from app.users.models import User


class MembershipService:
    def __init__(
        self,
        membership_repo: MembershipRepository,
        invitation_repo: InvitationRepository,
        org_repo: OrganizationRepository,
        user_repo: UserRepository,
        rbac_repo: RBACRepository,
        rbac_service: RBACService,
        audit_repo: AuditRepository,
    ) -> None:
        self._membership_repo = membership_repo
        self._invitation_repo = invitation_repo
        self._org_repo = org_repo
        self._user_repo = user_repo
        self._rbac_repo = rbac_repo
        self._rbac_service = rbac_service
        self._audit_repo = audit_repo

    async def get_membership(
        self, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> OrganizationMembership:
        membership = await self._membership_repo.get_for_organization(
            organization_id, membership_id
        )
        if membership is None:
            raise AppError(
                code="NOT_FOUND",
                message="Membership not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return membership

    async def list_members(
        self, organization_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[OrganizationMembership], int]:
        members = await self._membership_repo.list_for_organization(
            organization_id, offset=offset, limit=limit
        )
        total = await self._membership_repo.count_for_organization(organization_id)
        return members, total

    async def get_member_roles(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[UserRole]:
        return await self._rbac_repo.get_org_scoped_roles(user_id, organization_id)

    # ------------------------------------------------------------------
    # Invitations
    # ------------------------------------------------------------------

    async def invite_member(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        role_name: str,
        actor_user_id: uuid.UUID,
        base_url: str,
    ) -> OrganizationInvitation:
        org = await self._org_repo.get_by_id(organization_id)
        if org is None:
            raise AppError(
                code="NOT_FOUND",
                message="Organization not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if org.status != OrganizationStatus.ACTIVE:
            raise AppError(
                code="ORGANIZATION_SUSPENDED",
                message="Cannot invite members to a non-active organization.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        role = await self._rbac_repo.get_role_by_name(role_name)
        if role is None:
            raise AppError(
                code="NOT_FOUND",
                message=f"Role '{role_name}' not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if role.name == p.SUPER_ADMIN:
            raise AppError(
                code="INVALID_ROLE",
                message="SUPER_ADMIN is a platform-only role and cannot be assigned "
                "within an organization.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        normalized_email = User.normalize_email(email)
        existing_user = await self._user_repo.get_by_email(normalized_email)
        if existing_user:
            existing_membership = await self._membership_repo.get_by_user_and_org(
                existing_user.id, organization_id
            )
            if existing_membership and existing_membership.status == MembershipStatus.ACTIVE:
                raise AppError(
                    code="ALREADY_MEMBER",
                    message="This user is already a member of the organization.",
                    status_code=status.HTTP_409_CONFLICT,
                )

        settings = get_settings()
        raw_token, token_hash = generate_otp_token()
        expires_at = datetime.now(tz=UTC) + timedelta(
            hours=settings.organization_invitation_expire_hours
        )
        invitation = await self._invitation_repo.create(
            organization_id=organization_id,
            email=normalized_email,
            role_id=role.id,
            token_hash=token_hash,
            expires_at=expires_at,
            invited_by=actor_user_id,
        )

        invite_url = f"{base_url}/organizations/invitations/accept?token={raw_token}"
        email_service = get_email_service()
        await email_service.send_organization_invitation(
            to_email=normalized_email,
            organization_name=org.name,
            invite_url=invite_url,
            expires_hours=settings.organization_invitation_expire_hours,
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.MEMBER_INVITED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"email": normalized_email, "role": role_name},
        )
        return invitation

    async def accept_invitation(
        self,
        *,
        raw_token: str,
        password: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> OrganizationMembership:
        token_hash = hash_otp_token(raw_token)
        invitation = await self._invitation_repo.get_valid_by_token_hash(token_hash)
        if invitation is None:
            raise AppError(
                code="INVALID_TOKEN",
                message="Invitation is invalid or has expired.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        user = await self._user_repo.get_by_email(invitation.email)
        if user is None:
            if not password:
                raise AppError(
                    code="PASSWORD_REQUIRED",
                    message="A password is required to create your account.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            violations = check_password_policy(password)
            if violations:
                raise AppError(
                    code="WEAK_PASSWORD",
                    message=violations[0],
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            user = await self._user_repo.create(
                email=invitation.email,
                password_hash=hash_password(password),
                first_name=first_name or "Invited",
                last_name=last_name or "User",
                is_email_verified=True,
            )

        existing_membership = await self._membership_repo.get_by_user_and_org(
            user.id, invitation.organization_id
        )
        if existing_membership and existing_membership.status == MembershipStatus.ACTIVE:
            raise AppError(
                code="ALREADY_MEMBER",
                message="You are already a member of this organization.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if existing_membership:
            await self._membership_repo.update_status(
                invitation.organization_id, existing_membership.id, MembershipStatus.ACTIVE
            )
            membership = existing_membership
            membership.status = MembershipStatus.ACTIVE
        else:
            membership = await self._membership_repo.create(
                organization_id=invitation.organization_id,
                user_id=user.id,
                status=MembershipStatus.ACTIVE,
            )

        invited_role = await self._rbac_repo.get_role_by_id(invitation.role_id)
        if invited_role is None:
            raise AppError(
                code="NOT_FOUND",
                message="The role attached to this invitation no longer exists.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        await self._rbac_service.assign_role(
            actor_user_id=invitation.invited_by or user.id,
            target_user_id=user.id,
            role_name=invited_role.name,
            organization_id=invitation.organization_id,
        )
        await self._invitation_repo.mark_accepted(invitation.id)

        await self._audit_repo.log_event(
            event_type=AuditEventType.MEMBER_ACCEPTED,
            actor_user_id=user.id,
            target_user_id=user.id,
            organization_id=invitation.organization_id,
            metadata={"email": invitation.email},
        )
        return membership

    # ------------------------------------------------------------------
    # Membership lifecycle
    # ------------------------------------------------------------------

    async def update_member_status(
        self,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        new_status: MembershipStatus,
        actor_user_id: uuid.UUID,
    ) -> OrganizationMembership:
        membership = await self.get_membership(organization_id, membership_id)
        old_status = membership.status
        if old_status == new_status:
            return membership

        await self._membership_repo.update_status(organization_id, membership_id, new_status)
        membership.status = new_status

        event_type = (
            AuditEventType.MEMBER_SUSPENDED
            if new_status == MembershipStatus.SUSPENDED
            else AuditEventType.MEMBER_UPDATED
        )
        await self._audit_repo.log_event(
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=membership.user_id,
            organization_id=organization_id,
            metadata={"old_status": old_status.value, "new_status": new_status.value},
        )
        return membership

    async def remove_member(
        self,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        actor_user_id: uuid.UUID,
    ) -> None:
        membership = await self.get_membership(organization_id, membership_id)

        # Revoke every org-scoped role this user holds in this org — removing
        # membership must also remove access, not just flip a status flag.
        org_roles = await self._rbac_repo.get_org_scoped_roles(
            membership.user_id, organization_id
        )
        for user_role in org_roles:
            await self._rbac_repo.remove_role(user_role.id)

        await self._membership_repo.update_status(
            organization_id, membership_id, MembershipStatus.REMOVED
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.MEMBER_REMOVED,
            actor_user_id=actor_user_id,
            target_user_id=membership.user_id,
            organization_id=organization_id,
        )

    # ------------------------------------------------------------------
    # Org-scoped role assignment
    # ------------------------------------------------------------------

    async def assign_org_role(
        self,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        role_name: str,
        actor_user_id: uuid.UUID,
        actor_is_platform_super_admin: bool,
    ) -> UserRole:
        membership = await self.get_membership(organization_id, membership_id)

        if membership.user_id == actor_user_id and not actor_is_platform_super_admin:
            raise AppError(
                code="SELF_ROLE_MODIFICATION_DENIED",
                message="You cannot modify your own role assignment.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if role_name == p.SUPER_ADMIN:
            raise AppError(
                code="INVALID_ROLE",
                message="SUPER_ADMIN is a platform-only role and cannot be assigned "
                "within an organization.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        return await self._rbac_service.assign_role(
            actor_user_id=actor_user_id,
            target_user_id=membership.user_id,
            role_name=role_name,
            organization_id=organization_id,
        )

    async def remove_org_role(
        self,
        *,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        user_role_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_is_platform_super_admin: bool,
    ) -> None:
        membership = await self.get_membership(organization_id, membership_id)

        if membership.user_id == actor_user_id and not actor_is_platform_super_admin:
            raise AppError(
                code="SELF_ROLE_MODIFICATION_DENIED",
                message="You cannot modify your own role assignment.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        user_role = await self._rbac_repo.get_user_role_by_id(user_role_id)
        if (
            user_role is None
            or user_role.user_id != membership.user_id
            or user_role.organization_id != organization_id
        ):
            raise AppError(
                code="NOT_FOUND",
                message="Role assignment not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        await self._rbac_service.remove_role(
            actor_user_id=actor_user_id, user_role_id=user_role_id
        )
