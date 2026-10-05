"""Enrollment token repository — the ONLY layer that constructs SQLAlchemy
queries for EnrollmentToken.

Tenant-isolation rule (matches app/repositories/device_repository.py):
every method requires `organization_id` and filters on it — no bare
`get_by_id`. The one exception is `get_by_token_hash`, used only by the
unauthenticated device-registration endpoint, which necessarily doesn't
know an organization_id yet — it derives one FROM the token, which is why
the token itself must be unguessable (see app/enrollment/identity.py).
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enrollment.models import EnrollmentToken, EnrollmentTokenStatus


class EnrollmentTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, token_id: uuid.UUID
    ) -> EnrollmentToken | None:
        result = await self._session.execute(
            select(EnrollmentToken).where(
                EnrollmentToken.id == token_id,
                EnrollmentToken.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_token_hash(self, token_hash: str) -> EnrollmentToken | None:
        """Unauthenticated lookup used only by device self-registration.
        Deliberately not organization-scoped — the caller has no
        organization context yet; the token hash itself is the lookup key."""
        result = await self._session.execute(
            select(EnrollmentToken).where(EnrollmentToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def get_by_provider_enrollment_reference_unscoped(
        self, reference: str
    ) -> EnrollmentToken | None:
        """Phase 11: the enrollment-token counterpart to
        `ProviderDeviceLinkRepository.get_by_provider_device_id_unscoped` —
        a real Android device's own resource reports back the exact
        `enrollmentTokenName` it was enrolled with (see
        app/platform/android/client.py::get_device_resource), never an
        organization id, so resolving "which organization does this
        brand-new device belong to" necessarily starts from a cross-tenant
        lookup keyed on that reference. Safe because
        `provider_enrollment_reference` is the provider's own globally
        unique resource name for one specific minted credential — no
        ambiguous-match case to guard against the way
        `get_by_provider_device_id_unscoped` does. Returns any status, not
        just ACTIVE — callers must apply `_effective_token_status`
        themselves so an expired/revoked/exhausted token is rejected with
        the same freshness check `register_device` already applies, not a
        stale cached status."""
        result = await self._session.execute(
            select(EnrollmentToken).where(
                EnrollmentToken.provider_enrollment_reference == reference
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> list[EnrollmentToken]:
        result = await self._session.execute(
            select(EnrollmentToken)
            .where(EnrollmentToken.organization_id == organization_id)
            .order_by(EnrollmentToken.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(self, organization_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(EnrollmentToken)
            .where(EnrollmentToken.organization_id == organization_id)
        )
        return int(result.scalar_one())

    async def create(self, organization_id: uuid.UUID, **fields: object) -> EnrollmentToken:
        token = EnrollmentToken(organization_id=organization_id, **fields)
        self._session.add(token)
        await self._session.flush()
        await self._session.refresh(token)
        return token

    async def increment_use_count(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(EnrollmentToken)
            .where(EnrollmentToken.id == token_id)
            .values(use_count=EnrollmentToken.use_count + 1)
        )

    async def set_status(
        self, organization_id: uuid.UUID, token_id: uuid.UUID, status: EnrollmentTokenStatus
    ) -> None:
        await self._session.execute(
            update(EnrollmentToken)
            .where(
                EnrollmentToken.id == token_id,
                EnrollmentToken.organization_id == organization_id,
            )
            .values(status=status)
        )
