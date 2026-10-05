"""DeviceEnrollment repository — the ONLY layer that constructs SQLAlchemy
queries for DeviceEnrollment. Tenant-isolation rule as in
app/repositories/device_repository.py — every method requires
organization_id and filters on it."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enrollment.models import DeviceEnrollment, DeviceEnrollmentStatus


class DeviceEnrollmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, enrollment_id: uuid.UUID
    ) -> DeviceEnrollment | None:
        result = await self._session.execute(
            select(DeviceEnrollment).where(
                DeviceEnrollment.id == enrollment_id,
                DeviceEnrollment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_device_id(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceEnrollment | None:
        result = await self._session.execute(
            select(DeviceEnrollment).where(
                DeviceEnrollment.device_id == device_id,
                DeviceEnrollment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def create(self, organization_id: uuid.UUID, **fields: object) -> DeviceEnrollment:
        enrollment = DeviceEnrollment(organization_id=organization_id, **fields)
        self._session.add(enrollment)
        await self._session.flush()
        await self._session.refresh(enrollment)
        return enrollment

    async def update_for_organization(
        self, enrollment: DeviceEnrollment, **fields: object
    ) -> DeviceEnrollment:
        for key, value in fields.items():
            setattr(enrollment, key, value)
        await self._session.flush()
        await self._session.refresh(enrollment)
        return enrollment

    async def set_status(
        self,
        organization_id: uuid.UUID,
        enrollment_id: uuid.UUID,
        status: DeviceEnrollmentStatus,
    ) -> None:
        await self._session.execute(
            update(DeviceEnrollment)
            .where(
                DeviceEnrollment.id == enrollment_id,
                DeviceEnrollment.organization_id == organization_id,
            )
            .values(status=status)
        )
