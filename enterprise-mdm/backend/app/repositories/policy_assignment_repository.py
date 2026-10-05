"""PolicyAssignment repository — the ONLY layer that constructs SQLAlchemy
queries for this model. Tenant-isolation rule as elsewhere."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.policies.models import PolicyAssignment, PolicyScopeType


class PolicyAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, assignment_id: uuid.UUID
    ) -> PolicyAssignment | None:
        result = await self._session.execute(
            select(PolicyAssignment).where(
                PolicyAssignment.id == assignment_id,
                PolicyAssignment.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_scope(
        self,
        organization_id: uuid.UUID,
        policy_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> PolicyAssignment | None:
        result = await self._session.execute(
            select(PolicyAssignment).where(
                PolicyAssignment.organization_id == organization_id,
                PolicyAssignment.policy_id == policy_id,
                PolicyAssignment.scope_type == scope_type,
                PolicyAssignment.scope_id == scope_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_policy(
        self, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> list[PolicyAssignment]:
        result = await self._session.execute(
            select(PolicyAssignment).where(
                PolicyAssignment.organization_id == organization_id,
                PolicyAssignment.policy_id == policy_id,
            )
        )
        return list(result.scalars().all())

    async def list_for_scope(
        self,
        organization_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> list[PolicyAssignment]:
        """Every policy assigned to exactly this scope entry — used by
        PolicyResolutionService to gather the candidate set for
        device/device-group/user/organization precedence resolution."""
        result = await self._session.execute(
            select(PolicyAssignment).where(
                PolicyAssignment.organization_id == organization_id,
                PolicyAssignment.scope_type == scope_type,
                PolicyAssignment.scope_id == scope_id,
            )
        )
        return list(result.scalars().all())

    async def count_for_policy(self, organization_id: uuid.UUID, policy_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PolicyAssignment)
            .where(
                PolicyAssignment.organization_id == organization_id,
                PolicyAssignment.policy_id == policy_id,
            )
        )
        return int(result.scalar_one())

    async def create(self, organization_id: uuid.UUID, **fields: object) -> PolicyAssignment:
        assignment = PolicyAssignment(organization_id=organization_id, **fields)
        self._session.add(assignment)
        await self._session.flush()
        await self._session.refresh(assignment)
        return assignment

    async def delete(self, assignment: PolicyAssignment) -> None:
        await self._session.delete(assignment)
        await self._session.flush()
