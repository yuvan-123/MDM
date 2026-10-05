"""ApplicationDeployment repository — the ONLY layer that constructs
SQLAlchemy queries for this model (Phase 14). Tenant-isolation rule as
elsewhere: every method takes organization_id and filters on it."""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.models import ApplicationDeployment, ApplicationDeploymentStatus

_RowT = TypeVar("_RowT", bound=tuple[object, ...])

# Not yet resolved to a final outcome — used to find deployments whose
# underlying DeviceCommand might have progressed since it was last checked.
_IN_FLIGHT_STATUSES = frozenset(
    {ApplicationDeploymentStatus.PENDING, ApplicationDeploymentStatus.INSTALLING}
)


class ApplicationDeploymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, deployment_id: uuid.UUID
    ) -> ApplicationDeployment | None:
        result = await self._session.execute(
            select(ApplicationDeployment).where(
                ApplicationDeployment.id == deployment_id,
                ApplicationDeployment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_device_and_application(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, application_id: uuid.UUID
    ) -> ApplicationDeployment | None:
        result = await self._session.execute(
            select(ApplicationDeployment).where(
                ApplicationDeployment.organization_id == organization_id,
                ApplicationDeployment.device_id == device_id,
                ApplicationDeployment.application_id == application_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_in_flight_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[ApplicationDeployment]:
        """Used by ApplicationService.reconcile_device_deployments (called
        from every compliance evaluation, the same choke point Phase 13's
        violation reconciliation already runs from) to find deployments
        whose underlying DeviceCommand may have reached a terminal state
        since it was last observed."""
        result = await self._session.execute(
            select(ApplicationDeployment).where(
                ApplicationDeployment.organization_id == organization_id,
                ApplicationDeployment.device_id == device_id,
                ApplicationDeployment.status.in_(_IN_FLIGHT_STATUSES),
            )
        )
        return list(result.scalars().all())

    async def list_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[ApplicationDeployment]:
        result = await self._session.execute(
            select(ApplicationDeployment).where(
                ApplicationDeployment.organization_id == organization_id,
                ApplicationDeployment.device_id == device_id,
            )
        )
        return list(result.scalars().all())

    def _apply_filters(
        self,
        query: Select[_RowT],
        *,
        device_id: uuid.UUID | None,
        status: ApplicationDeploymentStatus | None,
    ) -> Select[_RowT]:
        if device_id is not None:
            query = query.where(ApplicationDeployment.device_id == device_id)
        if status is not None:
            query = query.where(ApplicationDeployment.status == status)
        return query

    async def list_for_application(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        status: ApplicationDeploymentStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ApplicationDeployment]:
        query = self._apply_filters(
            select(ApplicationDeployment).where(
                ApplicationDeployment.organization_id == organization_id,
                ApplicationDeployment.application_id == application_id,
            ),
            device_id=device_id,
            status=status,
        )
        result = await self._session.execute(
            query.order_by(ApplicationDeployment.updated_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_application(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        status: ApplicationDeploymentStatus | None = None,
    ) -> int:
        query = self._apply_filters(
            select(func.count())
            .select_from(ApplicationDeployment)
            .where(
                ApplicationDeployment.organization_id == organization_id,
                ApplicationDeployment.application_id == application_id,
            ),
            device_id=device_id,
            status=status,
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def create(self, organization_id: uuid.UUID, **fields: object) -> ApplicationDeployment:
        deployment = ApplicationDeployment(organization_id=organization_id, **fields)
        self._session.add(deployment)
        await self._session.flush()
        await self._session.refresh(deployment)
        return deployment

    async def update(
        self, deployment: ApplicationDeployment, **fields: object
    ) -> ApplicationDeployment:
        for key, value in fields.items():
            setattr(deployment, key, value)
        await self._session.flush()
        await self._session.refresh(deployment)
        return deployment
