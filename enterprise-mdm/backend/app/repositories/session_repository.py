"""Session repository — all queries for the sessions table.

Refresh-token reuse detection logic is in the auth service; this
repository only provides the data access primitives.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Session


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        refresh_token_hash: str,
        expires_at: datetime,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_identifier: str | None = None,
    ) -> Session:
        now = datetime.now(tz=UTC)
        db_session = Session(
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            created_at=now,
            expires_at=expires_at,
            last_used_at=now,
            ip_address=ip_address,
            user_agent=user_agent,
            device_identifier=device_identifier,
        )
        self._session.add(db_session)
        await self._session.flush()
        await self._session.refresh(db_session)
        return db_session

    async def get_by_id(self, session_id: uuid.UUID) -> Session | None:
        result = await self._session.execute(
            select(Session).where(Session.id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_id(self, session_id: uuid.UUID) -> Session | None:
        """Return session only if it is not revoked and not expired."""
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            select(Session).where(
                Session.id == session_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_refresh_token_hash(self, token_hash: str) -> Session | None:
        """Look up by refresh token hash — used during token rotation.

        Returns the session regardless of its revoked/expired state so the
        auth service can detect reuse (revoked hash lookup = attack signal).
        """
        result = await self._session.execute(
            select(Session).where(Session.refresh_token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke(self, session_id: uuid.UUID) -> None:
        """Soft-revoke a single session."""
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(revoked_at=datetime.now(tz=UTC))
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        """Revoke ALL active sessions for a user (logout-all, pw change, reuse)."""
        now = datetime.now(tz=UTC)
        await self._session.execute(
            update(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def rotate_refresh_token(
        self,
        session_id: uuid.UUID,
        new_token_hash: str,
        new_expires_at: datetime,
    ) -> None:
        """Replace the refresh token hash in-place (rotation)."""
        await self._session.execute(
            update(Session)
            .where(Session.id == session_id)
            .values(
                refresh_token_hash=new_token_hash,
                last_used_at=datetime.now(tz=UTC),
                expires_at=new_expires_at,
            )
        )

    async def list_active_for_user(self, user_id: uuid.UUID) -> list[Session]:
        """Return all non-revoked, non-expired sessions for a user."""
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.revoked_at.is_(None),
                Session.expires_at > now,
            )
            .order_by(Session.last_used_at.desc())
        )
        return list(result.scalars().all())
