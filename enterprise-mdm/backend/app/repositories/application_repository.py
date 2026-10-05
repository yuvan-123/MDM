"""Application catalog repository — the ONLY layer that constructs
SQLAlchemy queries for `Application` (Phase 14). Tenant-isolation rule as
elsewhere: every method takes organization_id and filters on it."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.models import Application
from app.devices.enums import DevicePlatform


class ApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> Application | None:
        result = await self._session.execute(
            select(Application).where(
                Application.id == application_id,
                Application.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        platform: DevicePlatform | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Application]:
        query = select(Application).where(Application.organization_id == organization_id)
        if platform is not None:
            query = query.where(Application.platform == platform)
        result = await self._session.execute(
            query.order_by(Application.name).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(
        self, organization_id: uuid.UUID, *, platform: DevicePlatform | None = None
    ) -> int:
        query = (
            select(func.count())
            .select_from(Application)
            .where(Application.organization_id == organization_id)
        )
        if platform is not None:
            query = query.where(Application.platform == platform)
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def exists_by_identifier(
        self,
        organization_id: uuid.UUID,
        *,
        platform: DevicePlatform,
        package_name: str | None,
        bundle_identifier: str | None,
    ) -> bool:
        query = (
            select(func.count())
            .select_from(Application)
            .where(
                Application.organization_id == organization_id,
                Application.platform == platform,
            )
        )
        if package_name is not None:
            query = query.where(Application.package_name == package_name)
        if bundle_identifier is not None:
            query = query.where(Application.bundle_identifier == bundle_identifier)
        result = await self._session.execute(query)
        return int(result.scalar_one()) > 0

    async def create(self, organization_id: uuid.UUID, **fields: object) -> Application:
        application = Application(organization_id=organization_id, **fields)
        self._session.add(application)
        await self._session.flush()
        await self._session.refresh(application)
        return application

    async def update_for_organization(
        self, application: Application, **fields: object
    ) -> Application:
        for key, value in fields.items():
            setattr(application, key, value)
        await self._session.flush()
        await self._session.refresh(application)
        return application

    async def delete(self, application: Application) -> None:
        await self._session.delete(application)
        await self._session.flush()
