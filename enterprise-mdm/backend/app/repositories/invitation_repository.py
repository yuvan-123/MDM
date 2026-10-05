"""Organization invitation repository.

Follows the same shape as TokenRepository's password-reset methods:
create / get_valid_by_token_hash (filtered by status + expiry) / mark_*.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import InvitationStatus, OrganizationInvitation


class InvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        organization_id: uuid.UUID,
        email: str,
        role_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
        invited_by: uuid.UUID | None,
    ) -> OrganizationInvitation:
        invitation = OrganizationInvitation(
            organization_id=organization_id,
            email=email,
            role_id=role_id,
            token_hash=token_hash,
            status=InvitationStatus.PENDING,
            invited_by=invited_by,
            created_at=datetime.now(tz=UTC),
            expires_at=expires_at,
        )
        self._session.add(invitation)
        await self._session.flush()
        await self._session.refresh(invitation)
        return invitation

    async def get_valid_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None:
        """Return the invitation only if it is PENDING and not expired."""
        result = await self._session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.token_hash == token_hash,
                OrganizationInvitation.status == InvitationStatus.PENDING,
                OrganizationInvitation.expires_at > datetime.now(tz=UTC),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_organization(
        self, organization_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> OrganizationInvitation | None:
        result = await self._session.execute(
            select(OrganizationInvitation).where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def mark_accepted(self, invitation_id: uuid.UUID) -> None:
        await self._session.execute(
            update(OrganizationInvitation)
            .where(OrganizationInvitation.id == invitation_id)
            .values(status=InvitationStatus.ACCEPTED, accepted_at=datetime.now(tz=UTC))
        )

    async def mark_revoked(self, invitation_id: uuid.UUID) -> None:
        await self._session.execute(
            update(OrganizationInvitation)
            .where(OrganizationInvitation.id == invitation_id)
            .values(status=InvitationStatus.REVOKED)
        )

    async def list_for_organization(
        self, organization_id: uuid.UUID
    ) -> list[OrganizationInvitation]:
        result = await self._session.execute(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.organization_id == organization_id)
            .order_by(OrganizationInvitation.created_at.desc())
        )
        return list(result.scalars().all())
