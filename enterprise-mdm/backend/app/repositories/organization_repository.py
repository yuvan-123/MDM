"""Organization repository — the ONLY layer that constructs SQLAlchemy queries for organizations."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization, OrganizationStatus


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, organization_id: uuid.UUID) -> Organization | None:
        result = await self._session.execute(
            select(Organization).where(Organization.id == organization_id)
        )
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Organization | None:
        result = await self._session.execute(
            select(Organization).where(Organization.slug == slug)
        )
        return result.scalar_one_or_none()

    async def slug_exists(self, slug: str) -> bool:
        result = await self._session.execute(
            select(func.count()).select_from(Organization).where(Organization.slug == slug)
        )
        return int(result.scalar_one()) > 0

    async def create(
        self,
        *,
        name: str,
        slug: str,
        status: OrganizationStatus = OrganizationStatus.ACTIVE,
    ) -> Organization:
        org = Organization(name=name, slug=slug, status=status)
        self._session.add(org)
        await self._session.flush()
        await self._session.refresh(org)
        return org

    async def update(self, organization: Organization, **fields: object) -> Organization:
        for key, value in fields.items():
            setattr(organization, key, value)
        await self._session.flush()
        # Refresh: `updated_at` is server-computed (onupdate=func.now()) and
        # gets marked expired after flush. Without an explicit awaited
        # refresh here, a later synchronous Pydantic model_validate() on
        # this object would attempt an implicit lazy-load outside of an
        # async context and raise MissingGreenlet.
        await self._session.refresh(organization)
        return organization

    async def update_status(
        self, organization_id: uuid.UUID, status: OrganizationStatus
    ) -> None:
        await self._session.execute(
            update(Organization)
            .where(Organization.id == organization_id)
            .values(status=status)
        )

    async def refresh(self, organization: Organization) -> None:
        """Re-fetch every attribute from the DB via an awaited query.

        Needed after any bare `update()` SQL statement (as opposed to
        setattr()+flush()) — the ORM has no way to locally know the new
        `updated_at` value, so it marks the object's attributes expired.
        A subsequent synchronous Pydantic model_validate() on that object
        would otherwise attempt an implicit lazy-load outside of an async
        context and raise MissingGreenlet.
        """
        await self._session.refresh(organization)

    async def list_organizations(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Organization]:
        result = await self._session.execute(
            select(Organization)
            .order_by(Organization.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_organizations(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Organization))
        return int(result.scalar_one())

    async def list_by_ids(self, organization_ids: list[uuid.UUID]) -> list[Organization]:
        if not organization_ids:
            return []
        result = await self._session.execute(
            select(Organization).where(Organization.id.in_(organization_ids))
        )
        return list(result.scalars().all())
