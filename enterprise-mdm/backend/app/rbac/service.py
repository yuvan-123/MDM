"""RBAC service — role/permission management.

This service handles role assignment, removal, and the resolution of a
user's effective permission set. It never constructs database queries
directly — that is done exclusively by RBACRepository.
"""

from __future__ import annotations

import uuid

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.rbac.models import Role, UserRole
from app.rbac.permissions import ALL_PERMISSIONS, BUILT_IN_ROLES
from app.repositories.audit_repository import AuditRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.user_repository import UserRepository


class RBACService:
    def __init__(
        self,
        rbac_repo: RBACRepository,
        user_repo: UserRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._rbac_repo = rbac_repo
        self._user_repo = user_repo
        self._audit_repo = audit_repo

    async def get_user_permissions(self, user_id: uuid.UUID) -> frozenset[str]:
        """Return all permission names the user holds across all their roles."""
        return await self._rbac_repo.get_permissions_for_user(user_id)

    async def get_user_roles(self, user_id: uuid.UUID) -> list[UserRole]:
        return await self._rbac_repo.get_roles_for_user(user_id)

    async def list_roles(self) -> list[Role]:
        return await self._rbac_repo.get_all_roles()

    async def assign_role(
        self,
        *,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        role_name: str,
        organization_id: uuid.UUID | None = None,
    ) -> UserRole:
        # Verify target user exists
        target = await self._user_repo.get_by_id(target_user_id)
        if target is None:
            raise AppError(
                code="NOT_FOUND",
                message="User not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        role = await self._rbac_repo.get_role_by_name(role_name)
        if role is None:
            raise AppError(
                code="NOT_FOUND",
                message=f"Role '{role_name}' not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        user_role = await self._rbac_repo.assign_role(
            user_id=target_user_id,
            role_id=role.id,
            granted_by=actor_user_id,
            organization_id=organization_id,
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.ROLE_ASSIGNED,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            metadata={
                "role": role_name,
                "organization_id": str(organization_id) if organization_id else None,
            },
        )

        return user_role

    async def remove_role(
        self,
        *,
        actor_user_id: uuid.UUID,
        user_role_id: uuid.UUID,
    ) -> None:
        user_role = await self._rbac_repo.get_user_role_by_id(user_role_id)
        if user_role is None:
            raise AppError(
                code="NOT_FOUND",
                message="Role assignment not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        target_user_id = user_role.user_id
        role = user_role.role
        removed = await self._rbac_repo.remove_role(user_role_id)
        if removed:
            await self._audit_repo.log_event(
                event_type=AuditEventType.ROLE_REMOVED,
                actor_user_id=actor_user_id,
                target_user_id=target_user_id,
                metadata={"role": role.name if role else str(user_role.role_id)},
            )

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    async def seed_roles_and_permissions(self) -> None:
        """Idempotently seed built-in permissions and roles.

        Called by scripts/seed_admin.py. Safe to re-run — uses upsert logic.
        """
        for perm_def in ALL_PERMISSIONS:
            await self._rbac_repo.upsert_permission(
                name=perm_def.name,
                display_name=perm_def.display_name,
                resource=perm_def.resource,
                action=perm_def.action,
            )

        for role_def in BUILT_IN_ROLES:
            await self._rbac_repo.upsert_role(
                name=role_def.name,
                display_name=role_def.display_name,
                description=role_def.description,
                is_system_role=True,
                permissions=list(role_def.permissions),
            )
