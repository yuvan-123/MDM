"""Policy / PolicyVersion repository — the ONLY layer that constructs
SQLAlchemy queries for these models. Tenant-isolation rule as elsewhere."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.policies.models import Policy, PolicyStatus, PolicyVersion


class PolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> Policy | None:
        result = await self._session.execute(
            select(Policy)
            .options(selectinload(Policy.current_version))
            .where(Policy.id == policy_id, Policy.organization_id == organization_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        status: PolicyStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[Policy]:
        query = (
            select(Policy)
            .options(selectinload(Policy.current_version))
            .where(Policy.organization_id == organization_id)
            .execution_options(populate_existing=True)
        )
        if status is not None:
            query = query.where(Policy.status == status)
        result = await self._session.execute(
            query.order_by(Policy.name).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(
        self, organization_id: uuid.UUID, *, status: PolicyStatus | None = None
    ) -> int:
        query = (
            select(func.count())
            .select_from(Policy)
            .where(Policy.organization_id == organization_id)
        )
        if status is not None:
            query = query.where(Policy.status == status)
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def exists_by_name(self, organization_id: uuid.UUID, name: str) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(Policy)
            .where(Policy.organization_id == organization_id, Policy.name == name)
        )
        return int(result.scalar_one()) > 0

    async def create(self, organization_id: uuid.UUID, **fields: object) -> Policy:
        policy = Policy(organization_id=organization_id, **fields)
        self._session.add(policy)
        await self._session.flush()
        await self._session.refresh(policy)
        return policy

    async def update_for_organization(self, policy: Policy, **fields: object) -> Policy:
        for key, value in fields.items():
            setattr(policy, key, value)
        await self._session.flush()
        await self._session.refresh(policy)
        return policy

    async def delete(self, policy: Policy) -> None:
        await self._session.delete(policy)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    async def get_latest_version_number(self, policy_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.max(PolicyVersion.version)).where(PolicyVersion.policy_id == policy_id)
        )
        return int(result.scalar_one() or 0)

    async def create_version(
        self,
        *,
        policy_id: uuid.UUID,
        organization_id: uuid.UUID,
        version: int,
        configuration: dict[str, Any],
        created_at: datetime,
        created_by: uuid.UUID | None,
    ) -> PolicyVersion:
        pv = PolicyVersion(
            policy_id=policy_id,
            organization_id=organization_id,
            version=version,
            configuration=configuration,
            created_at=created_at,
            created_by=created_by,
        )
        self._session.add(pv)
        await self._session.flush()
        await self._session.refresh(pv)
        return pv

    async def get_version_for_organization(
        self, organization_id: uuid.UUID, version_id: uuid.UUID
    ) -> PolicyVersion | None:
        result = await self._session.execute(
            select(PolicyVersion).where(
                PolicyVersion.id == version_id,
                PolicyVersion.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_versions_for_policy(self, policy_id: uuid.UUID) -> list[PolicyVersion]:
        result = await self._session.execute(
            select(PolicyVersion)
            .where(PolicyVersion.policy_id == policy_id)
            .order_by(PolicyVersion.version.desc())
        )
        return list(result.scalars().all())
