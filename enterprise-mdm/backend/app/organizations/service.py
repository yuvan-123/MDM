"""Organization service — CRUD, slug generation, status transitions.

Constructs no raw SQL/ORM queries — delegates strictly to
OrganizationRepository and MembershipRepository.
"""

from __future__ import annotations

import re
import uuid

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.organizations.models import Organization, OrganizationStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.organization_repository import OrganizationRepository

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Normalize *name* into a URL-safe slug base.

    "Acme Corporation" -> "acme-corporation". Does not guarantee uniqueness
    — callers must combine with a collision-suffix loop against the
    repository (see OrganizationService.create_organization).
    """
    slug = _SLUG_INVALID_CHARS.sub("-", name.strip().lower()).strip("-")
    return slug or "organization"


class OrganizationService:
    def __init__(
        self,
        org_repo: OrganizationRepository,
        membership_repo: MembershipRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._org_repo = org_repo
        self._membership_repo = membership_repo
        self._audit_repo = audit_repo

    async def get_by_id(self, organization_id: uuid.UUID) -> Organization:
        org = await self._org_repo.get_by_id(organization_id)
        if org is None:
            raise AppError(
                code="NOT_FOUND",
                message="Organization not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return org

    async def _generate_unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug = base
        suffix = 2
        while await self._org_repo.slug_exists(slug):
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    async def create_organization(
        self, *, name: str, actor_user_id: uuid.UUID
    ) -> Organization:
        slug = await self._generate_unique_slug(name)
        org = await self._org_repo.create(name=name, slug=slug)

        await self._audit_repo.log_event(
            event_type=AuditEventType.ORGANIZATION_CREATED,
            actor_user_id=actor_user_id,
            organization_id=org.id,
            metadata={"name": org.name, "slug": org.slug},
        )
        return org

    async def update_organization(
        self,
        *,
        organization_id: uuid.UUID,
        name: str | None,
        actor_user_id: uuid.UUID,
    ) -> Organization:
        org = await self.get_by_id(organization_id)
        if name is not None and name != org.name:
            org = await self._org_repo.update(org, name=name)
            await self._audit_repo.log_event(
                event_type=AuditEventType.ORGANIZATION_UPDATED,
                actor_user_id=actor_user_id,
                organization_id=org.id,
                metadata={"name": org.name},
            )
        return org

    async def update_status(
        self,
        *,
        organization_id: uuid.UUID,
        new_status: OrganizationStatus,
        actor_user_id: uuid.UUID,
    ) -> Organization:
        org = await self.get_by_id(organization_id)
        old_status = org.status
        if old_status == new_status:
            return org

        await self._org_repo.update_status(organization_id, new_status)
        await self._org_repo.refresh(org)

        event_type = {
            OrganizationStatus.SUSPENDED: AuditEventType.ORGANIZATION_SUSPENDED,
            OrganizationStatus.DISABLED: AuditEventType.ORGANIZATION_DISABLED,
        }.get(new_status, AuditEventType.ORGANIZATION_UPDATED)

        await self._audit_repo.log_event(
            event_type=event_type,
            actor_user_id=actor_user_id,
            organization_id=org.id,
            metadata={"old_status": old_status.value, "new_status": new_status.value},
        )
        return org

    async def list_organizations(
        self, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[Organization], int]:
        """Platform-wide listing — callers must gate this to SUPER_ADMIN."""
        orgs = await self._org_repo.list_organizations(offset=offset, limit=limit)
        total = await self._org_repo.count_organizations()
        return orgs, total

    async def list_for_user(self, user_id: uuid.UUID) -> list[Organization]:
        """Organizations the given user has a membership in (any status)."""
        memberships = await self._membership_repo.list_for_user(user_id)
        return [m.organization for m in memberships]
