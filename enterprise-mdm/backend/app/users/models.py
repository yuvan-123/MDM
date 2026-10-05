"""User ORM model.

Status lifecycle:
  INVITED → ACTIVE (on first login / email verification)
  ACTIVE ↔ SUSPENDED (admin action)
  ACTIVE → LOCKED (automated after N failed logins)
  LOCKED → ACTIVE (admin unlock or TTL expiry)
  any → DISABLED (admin — soft delete)

MFA fields are present but MFA is NOT YET IMPLEMENTED (Phase TBD).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"
    LOCKED = "LOCKED"
    DISABLED = "DISABLED"


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Platform user / administrator account.

    organization_id is intentionally NOT on this model. Users are
    associated with organizations via the UserRole join table (Phase 3),
    which allows a user to hold roles in multiple organizations without
    duplicating the user record.
    """

    __tablename__ = "users"

    # --- Identity ---
    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        nullable=False,
        index=True,
        comment="Normalized (lowercase, stripped) email address.",
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # --- Credentials ---
    # NEVER expose this field through any API response schema.
    password_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Argon2id hash of the user's password. Never returned via API.",
    )

    # --- Status & verification ---
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"),
        nullable=False,
        default=UserStatus.ACTIVE,
        index=True,
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    # --- Login tracking ---
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="If set and in the future, account is temporarily locked.",
    )

    # --- MFA (PLANNED — NOT YET IMPLEMENTED) ---
    # Fields reserved so the schema does not need to be altered when MFA
    # (TOTP / WebAuthn) is added in a later phase.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    mfa_totp_secret: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Encrypted TOTP secret. PLANNED — NOT YET IMPLEMENTED.",
    )

    # --- Relationships (populated in later phases as foreign modules exist) ---
    sessions: Mapped[list[Session]] = relationship(  # noqa: F821
        "Session",
        back_populates="user",
        lazy="noload",
    )
    user_roles: Mapped[list[UserRole]] = relationship(  # noqa: F821
        "UserRole",
        foreign_keys="UserRole.user_id",
        back_populates="user",
        lazy="noload",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} status={self.status}>"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @staticmethod
    def normalize_email(email: str) -> str:
        """Normalize email to lowercase and strip whitespace."""
        return email.lower().strip()


# Circular import shim — models defined in other modules but referenced here.
from app.auth.models import Session  # noqa: E402, F401
from app.rbac.models import UserRole  # noqa: E402, F401
