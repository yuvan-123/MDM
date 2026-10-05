"""Auth ORM models: Session, PasswordResetToken, EmailVerificationToken.

Security rules enforced at the model level:
- Raw refresh tokens are NEVER stored. Only a secure hash (SHA-256) of the
  token is persisted in refresh_token_hash. The raw token is returned to
  the client once and then discarded server-side.
- Same principle for password reset and email verification tokens: only
  the hash is stored.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, UUIDPrimaryKeyMixin


class Session(Base, UUIDPrimaryKeyMixin):
    """Persistent session record enabling refresh-token rotation and revocation.

    Lifecycle:
      Created on login.
      last_used_at updated on each token refresh.
      revoked_at set on logout, logout-all, password change, or reuse detection.
      Expired sessions (expires_at < now) are treated as revoked.

    Reuse detection: if a refresh token hash is looked up but the session
    is already revoked, the auth service must treat this as a token-reuse
    attack: revoke all other sessions for the user and create a security
    event.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest of the raw refresh token. Raw token returned to
    # client once; never stored or logged.
    refresh_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hex of raw refresh token. Raw token never stored.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # Contextual metadata for security review / anomaly detection
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_identifier: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Optional opaque client device ID for future per-device session management.",
    )

    user: Mapped[User] = relationship(  # noqa: F821
        "User", back_populates="sessions"
    )

    @property
    def is_active(self) -> bool:
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return self.revoked_at is None and expires > datetime.now(tz=UTC)


class PasswordResetToken(Base, UUIDPrimaryKeyMixin):
    """Single-use password reset token.

    Only the SHA-256 hash of the raw token is stored. The raw token is
    included in the reset link emailed to the user and never persisted.
    """

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hex of raw reset token. Raw token emailed to user, never stored.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User")  # noqa: F821


class EmailVerificationToken(Base, UUIDPrimaryKeyMixin):
    """Single-use email verification token.

    Architecture present; enforcement (blocking login until verified) is
    controlled by Settings.email_verification_required.
    """

    __tablename__ = "email_verification_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User")  # noqa: F821


from app.users.models import User  # noqa: E402, F401
