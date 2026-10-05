"""DeviceCompliance / ComplianceResult repository — the ONLY layer that
constructs SQLAlchemy queries for these models. Tenant-isolation rule as
elsewhere."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.models import ComplianceResult, ComplianceStatus, DeviceCompliance


class ComplianceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Current-state cache
    # ------------------------------------------------------------------

    async def get_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceCompliance | None:
        result = await self._session.execute(
            select(DeviceCompliance).where(
                DeviceCompliance.device_id == device_id,
                DeviceCompliance.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        status: ComplianceStatus,
        policy_id: uuid.UUID | None,
        policy_version_id: uuid.UUID | None,
        details: list[dict[str, Any]] | None,
        evaluated_at: datetime,
    ) -> DeviceCompliance:
        existing = await self.get_for_device(organization_id, device_id)
        if existing is None:
            existing = DeviceCompliance(
                device_id=device_id,
                organization_id=organization_id,
                status=status,
                policy_id=policy_id,
                policy_version_id=policy_version_id,
                details=details,
                evaluated_at=evaluated_at,
            )
            self._session.add(existing)
        else:
            existing.status = status
            existing.policy_id = policy_id
            existing.policy_version_id = policy_version_id
            existing.details = details
            existing.evaluated_at = evaluated_at
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        status: ComplianceStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[DeviceCompliance]:
        query = select(DeviceCompliance).where(DeviceCompliance.organization_id == organization_id)
        if status is not None:
            query = query.where(DeviceCompliance.status == status)
        result = await self._session.execute(
            query.order_by(DeviceCompliance.evaluated_at.desc().nullslast())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_status(self, organization_id: uuid.UUID) -> dict[str, int]:
        result = await self._session.execute(
            select(DeviceCompliance.status, func.count())
            .where(DeviceCompliance.organization_id == organization_id)
            .group_by(DeviceCompliance.status)
        )
        return {row[0].value: row[1] for row in result.all()}

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    async def create_result(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        **fields: object,
    ) -> ComplianceResult:
        result = ComplianceResult(organization_id=organization_id, device_id=device_id, **fields)
        self._session.add(result)
        await self._session.flush()
        await self._session.refresh(result)
        return result

    async def list_results_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, limit: int = 20
    ) -> list[ComplianceResult]:
        result = await self._session.execute(
            select(ComplianceResult)
            .where(
                ComplianceResult.organization_id == organization_id,
                ComplianceResult.device_id == device_id,
            )
            .order_by(ComplianceResult.evaluated_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
