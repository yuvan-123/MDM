"""Token repository — password reset and email verification tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import EmailVerificationToken, PasswordResetToken


class TokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

    async def create_password_reset_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> PasswordResetToken:
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            created_at=datetime.now(tz=UTC),
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        await self._session.refresh(token)
        return token

    async def get_valid_reset_token(self, token_hash: str) -> PasswordResetToken | None:
        """Return the token only if it is unused and not yet expired."""
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def mark_reset_token_used(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(PasswordResetToken)
            .where(PasswordResetToken.id == token_id)
            .values(used_at=datetime.now(tz=UTC))
        )

    async def invalidate_reset_tokens_for_user(self, user_id: uuid.UUID) -> None:
        """Invalidate all pending reset tokens for a user (e.g. on pw change)."""
        await self._session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .values(used_at=datetime.now(tz=UTC))
        )

    # ------------------------------------------------------------------
    # Email verification
    # ------------------------------------------------------------------

    async def create_email_verification_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> EmailVerificationToken:
        token = EmailVerificationToken(
            user_id=user_id,
            token_hash=token_hash,
            created_at=datetime.now(tz=UTC),
            expires_at=expires_at,
        )
        self._session.add(token)
        await self._session.flush()
        await self._session.refresh(token)
        return token

    async def get_valid_verify_token(
        self, token_hash: str
    ) -> EmailVerificationToken | None:
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.token_hash == token_hash,
                EmailVerificationToken.used_at.is_(None),
                EmailVerificationToken.expires_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def mark_verify_token_used(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(EmailVerificationToken)
            .where(EmailVerificationToken.id == token_id)
            .values(used_at=datetime.now(tz=UTC))
        )
