"""RBAC repository — roles, permissions, and user-role assignments."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.rbac.models import Permission, Role, RolePermission, UserRole


class RBACRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------------

    async def get_all_roles(self) -> list[Role]:
        result = await self._session.execute(select(Role).order_by(Role.name))
        return list(result.scalars().all())

    async def get_role_by_name(self, name: str) -> Role | None:
        result = await self._session.execute(
            select(Role).where(Role.name == name)
        )
        return result.scalar_one_or_none()

    async def get_role_by_id(self, role_id: uuid.UUID) -> Role | None:
        result = await self._session.execute(
            select(Role).where(Role.id == role_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    async def get_all_permissions(self) -> list[Permission]:
        result = await self._session.execute(select(Permission).order_by(Permission.name))
        return list(result.scalars().all())

    async def get_permission_by_name(self, name: str) -> Permission | None:
        result = await self._session.execute(
            select(Permission).where(Permission.name == name)
        )
        return result.scalar_one_or_none()

    async def get_permissions_for_user(self, user_id: uuid.UUID) -> frozenset[str]:
        """Return the union of permissions from *platform-level* roles only
        (UserRole.organization_id IS NULL — e.g. SUPER_ADMIN).

        Phase 3: this deliberately excludes org-scoped role grants. Without
        this filter, a user holding e.g. ORGANIZATION_ADMIN scoped to one
        organization would have USER_CREATE etc. leak into CurrentUser's
        platform permission set and pass `require_permission` on platform
        routes like `/api/v1/users` — a cross-tenant privilege escalation.
        Org-scoped permissions are resolved separately via
        `get_org_scoped_permissions`, only within a validated
        OrganizationContext. Under Phase 2 every UserRole row had
        organization_id=NULL anyway, so this filter is a no-op for all
        pre-Phase-3 behavior.
        """
        result = await self._session.execute(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id, UserRole.organization_id.is_(None))
        )
        return frozenset(result.scalars().all())

    async def get_roles_for_user(self, user_id: uuid.UUID) -> list[UserRole]:
        result = await self._session.execute(
            select(UserRole)
            .options(selectinload(UserRole.role))
            .where(UserRole.user_id == user_id)
        )
        return list(result.scalars().all())

    async def get_org_scoped_permissions(
        self, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> frozenset[str]:
        """Return the union of permissions from roles the user holds
        specifically within *organization_id* (UserRole.organization_id
        == organization_id). Platform-level roles (organization_id IS
        NULL, e.g. SUPER_ADMIN) are NOT included here — callers that need
        to grant platform admins organization access check
        CurrentUser.permissions separately and bypass this method.
        """
        result = await self._session.execute(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(
                UserRole.user_id == user_id,
                UserRole.organization_id == organization_id,
            )
        )
        return frozenset(result.scalars().all())

    async def get_org_scoped_roles(
        self, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> list[UserRole]:
        result = await self._session.execute(
            select(UserRole)
            .options(selectinload(UserRole.role))
            .where(
                UserRole.user_id == user_id,
                UserRole.organization_id == organization_id,
            )
        )
        return list(result.scalars().all())

    async def user_has_platform_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        """True if *user_id* holds *role_name* at platform scope
        (UserRole.organization_id IS NULL). Used to detect SUPER_ADMIN for
        the organization-context bypass — an explicit role-name check
        rather than inferring it from a permission string, so it can't be
        spoofed by a custom role that happens to share a permission name.
        """
        result = await self._session.execute(
            select(func.count())
            .select_from(UserRole)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user_id,
                UserRole.organization_id.is_(None),
                Role.name == role_name,
            )
        )
        return int(result.scalar_one()) > 0

    # ------------------------------------------------------------------
    # User-role assignments
    # ------------------------------------------------------------------

    async def assign_role(
        self,
        *,
        user_id: uuid.UUID,
        role_id: uuid.UUID,
        granted_by: uuid.UUID | None,
        organization_id: uuid.UUID | None = None,
    ) -> UserRole:
        user_role = UserRole(
            user_id=user_id,
            role_id=role_id,
            organization_id=organization_id,
            granted_at=datetime.now(tz=UTC),
            granted_by=granted_by,
        )
        self._session.add(user_role)
        await self._session.flush()
        await self._session.refresh(user_role)
        return user_role

    async def get_user_role_by_id(self, user_role_id: uuid.UUID) -> UserRole | None:
        # Eager-loads .role: RBACService.remove_role reads user_role.role for
        # its audit log metadata, which would otherwise attempt a lazy load
        # outside of an async greenlet context and raise MissingGreenlet.
        result = await self._session.execute(
            select(UserRole)
            .options(selectinload(UserRole.role))
            .where(UserRole.id == user_role_id)
        )
        return result.scalar_one_or_none()

    async def remove_role(self, user_role_id: uuid.UUID) -> bool:
        """Delete the UserRole record. Returns True if it existed."""
        user_role = await self.get_user_role_by_id(user_role_id)
        if user_role is None:
            return False
        await self._session.delete(user_role)
        await self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Seeding helpers (used by scripts/seed_admin.py and migrations)
    # ------------------------------------------------------------------

    async def upsert_permission(
        self,
        *,
        name: str,
        display_name: str,
        resource: str,
        action: str,
    ) -> Permission:
        existing = await self.get_permission_by_name(name)
        if existing:
            return existing
        perm = Permission(
            name=name, display_name=display_name, resource=resource, action=action
        )
        self._session.add(perm)
        await self._session.flush()
        await self._session.refresh(perm)
        return perm

    async def upsert_role(
        self,
        *,
        name: str,
        display_name: str,
        description: str | None,
        is_system_role: bool,
        permissions: list[str],
    ) -> Role:
        existing = await self.get_role_by_name(name)
        if not existing:
            existing = Role(
                name=name,
                display_name=display_name,
                description=description,
                is_system_role=is_system_role,
            )
            self._session.add(existing)
            await self._session.flush()
            await self._session.refresh(existing)

        # Sync permissions: add missing, leave existing.
        current_perm_result = await self._session.execute(
            select(Permission.name)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .where(RolePermission.role_id == existing.id)
        )
        current_names = set(current_perm_result.scalars().all())

        for perm_name in permissions:
            if perm_name in current_names:
                continue
            perm = await self.get_permission_by_name(perm_name)
            if perm:
                self._session.add(
                    RolePermission(role_id=existing.id, permission_id=perm.id)
                )

        await self._session.flush()
        return existing
