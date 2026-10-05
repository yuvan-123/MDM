"""Organization membership repository.

Tenant-isolation rule (docs/architecture/multi-tenancy.md): every method
that fetches a specific membership takes `organization_id` explicitly and
filters on it — never a bare `get_by_id(membership_id)`. A membership that
belongs to a different organization must resolve to "not found", not to the
wrong tenant's data.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.organizations.models import MembershipStatus, OrganizationMembership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, membership_id: uuid.UUID
    ) -> OrganizationMembership | None:
        """Fetch a membership by ID, scoped to *organization_id*.

        Returns None if the membership doesn't exist OR belongs to a
        different organization — callers cannot distinguish the two, which
        is intentional (don't confirm cross-tenant existence).

        Uses populate_existing=True: if this membership was already loaded
        earlier in the same session (e.g. just created via `create()`,
        which never touches `.user`), SQLAlchemy's identity map would
        otherwise return that same cached instance without applying the
        `selectinload` option to it, leaving `.user` as None instead of
        eager-loaded. populate_existing forces this query's loader options
        to re-populate the identity-mapped object regardless of its prior
        state.
        """
        result = await self._session.execute(
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(
                OrganizationMembership.id == membership_id,
                OrganizationMembership.organization_id == organization_id,
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_user_and_org(
        self, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> OrganizationMembership | None:
        result = await self._session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[OrganizationMembership]:
        result = await self._session.execute(
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(OrganizationMembership.created_at.desc())
            .execution_options(populate_existing=True)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(self, organization_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
        )
        return int(result.scalar_one())

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        """A user's own memberships, across all organizations. Used to
        resolve `GET /organizations` (the caller's own org list) and to
        auto-default tenant context when a user belongs to exactly one org.
        """
        result = await self._session.execute(
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.organization))
            .where(OrganizationMembership.user_id == user_id)
        )
        return list(result.scalars().all())

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        status: MembershipStatus = MembershipStatus.ACTIVE,
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            organization_id=organization_id, user_id=user_id, status=status
        )
        self._session.add(membership)
        await self._session.flush()
        await self._session.refresh(membership)
        return membership

    async def update_status(
        self,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        status: MembershipStatus,
    ) -> None:
        await self._session.execute(
            update(OrganizationMembership)
            .where(
                OrganizationMembership.id == membership_id,
                OrganizationMembership.organization_id == organization_id,
            )
            .values(status=status)
        )
