"""PolicyViolation repository — the ONLY layer that constructs SQLAlchemy
queries for this model (Phase 13). Tenant-isolation rule as elsewhere:
every method takes organization_id and filters on it."""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance.models import PolicyViolation, ViolationStatus

_RowT = TypeVar("_RowT", bound=tuple[object, ...])

# Every status except RESOLVED — "still needs attention in some form,"
# used by ComplianceService's per-evaluation reconciliation to find every
# violation that might need to be resolved or re-checked, regardless of
# which sub-state it's currently in.
_ACTIVE_STATUSES = frozenset(
    {
        ViolationStatus.OPEN,
        ViolationStatus.REMEDIATION_PENDING,
        ViolationStatus.REMEDIATING,
        ViolationStatus.REMEDIATION_FAILED,
    }
)


class PolicyViolationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, violation_id: uuid.UUID
    ) -> PolicyViolation | None:
        result = await self._session.execute(
            select(PolicyViolation).where(
                PolicyViolation.id == violation_id,
                PolicyViolation.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_device_policy_rule(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        policy_id: uuid.UUID,
        rule_code: str,
    ) -> PolicyViolation | None:
        result = await self._session.execute(
            select(PolicyViolation).where(
                PolicyViolation.organization_id == organization_id,
                PolicyViolation.device_id == device_id,
                PolicyViolation.policy_id == policy_id,
                PolicyViolation.rule_code == rule_code,
            )
        )
        return result.scalar_one_or_none()

    async def list_active_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> list[PolicyViolation]:
        """Every non-RESOLVED violation for this device, regardless of
        which policy produced it — used by ComplianceService to detect
        "this violation's policy/rule no longer applies" on every
        evaluation, not just ones triggered by an assignment change."""
        result = await self._session.execute(
            select(PolicyViolation).where(
                PolicyViolation.organization_id == organization_id,
                PolicyViolation.device_id == device_id,
                PolicyViolation.status.in_(_ACTIVE_STATUSES),
            )
        )
        return list(result.scalars().all())

    async def create(self, organization_id: uuid.UUID, **fields: object) -> PolicyViolation:
        violation = PolicyViolation(organization_id=organization_id, **fields)
        self._session.add(violation)
        await self._session.flush()
        await self._session.refresh(violation)
        return violation

    async def update(self, violation: PolicyViolation, **fields: object) -> PolicyViolation:
        for key, value in fields.items():
            setattr(violation, key, value)
        await self._session.flush()
        await self._session.refresh(violation)
        return violation

    # ------------------------------------------------------------------
    # Admin API (list/filter)
    # ------------------------------------------------------------------

    def _apply_filters(
        self,
        query: Select[_RowT],
        *,
        device_id: uuid.UUID | None,
        policy_id: uuid.UUID | None,
        status: ViolationStatus | None,
    ) -> Select[_RowT]:
        if device_id is not None:
            query = query.where(PolicyViolation.device_id == device_id)
        if policy_id is not None:
            query = query.where(PolicyViolation.policy_id == policy_id)
        if status is not None:
            query = query.where(PolicyViolation.status == status)
        return query

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        policy_id: uuid.UUID | None = None,
        status: ViolationStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[PolicyViolation]:
        query = self._apply_filters(
            select(PolicyViolation).where(PolicyViolation.organization_id == organization_id),
            device_id=device_id,
            policy_id=policy_id,
            status=status,
        )
        result = await self._session.execute(
            query.order_by(PolicyViolation.last_seen_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        policy_id: uuid.UUID | None = None,
        status: ViolationStatus | None = None,
    ) -> int:
        query = self._apply_filters(
            select(func.count())
            .select_from(PolicyViolation)
            .where(PolicyViolation.organization_id == organization_id),
            device_id=device_id,
            policy_id=policy_id,
            status=status,
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())
