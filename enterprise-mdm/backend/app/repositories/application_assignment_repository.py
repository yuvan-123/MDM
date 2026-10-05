"""ApplicationAssignment repository — the ONLY layer that constructs
SQLAlchemy queries for this model (Phase 14). Mirrors
app/repositories/policy_assignment_repository.py's shape exactly (same
polymorphic scope_type/scope_id design)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.models import ApplicationAssignment
from app.policies.models import PolicyScopeType


class ApplicationAssignmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_scope(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> ApplicationAssignment | None:
        result = await self._session.execute(
            select(ApplicationAssignment).where(
                ApplicationAssignment.organization_id == organization_id,
                ApplicationAssignment.application_id == application_id,
                ApplicationAssignment.scope_type == scope_type,
                ApplicationAssignment.scope_id == scope_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_scope(
        self, organization_id: uuid.UUID, scope_type: PolicyScopeType, scope_id: uuid.UUID
    ) -> list[ApplicationAssignment]:
        """Used to resolve every application assignment applicable to one
        device at one scope level — see
        ApplicationService.resolve_assignments_for_device, which (unlike
        PolicyResolutionService) unions every matching assignment across
        all four scope levels rather than picking a single winner: multiple
        required apps from different scopes are not mutually exclusive the
        way policies are."""
        result = await self._session.execute(
            select(ApplicationAssignment).where(
                ApplicationAssignment.organization_id == organization_id,
                ApplicationAssignment.scope_type == scope_type,
                ApplicationAssignment.scope_id == scope_id,
            )
        )
        return list(result.scalars().all())

    async def list_for_application(
        self, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> list[ApplicationAssignment]:
        result = await self._session.execute(
            select(ApplicationAssignment).where(
                ApplicationAssignment.organization_id == organization_id,
                ApplicationAssignment.application_id == application_id,
            )
        )
        return list(result.scalars().all())

    async def count_for_application(
        self, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> int:
        assignments = await self.list_for_application(organization_id, application_id)
        return len(assignments)

    async def update(
        self, assignment: ApplicationAssignment, **fields: object
    ) -> ApplicationAssignment:
        for key, value in fields.items():
            setattr(assignment, key, value)
        await self._session.flush()
        await self._session.refresh(assignment)
        return assignment

    async def create(self, organization_id: uuid.UUID, **fields: object) -> ApplicationAssignment:
        assignment = ApplicationAssignment(organization_id=organization_id, **fields)
        self._session.add(assignment)
        await self._session.flush()
        await self._session.refresh(assignment)
        return assignment

    async def delete(self, assignment: ApplicationAssignment) -> None:
        await self._session.delete(assignment)
        await self._session.flush()
