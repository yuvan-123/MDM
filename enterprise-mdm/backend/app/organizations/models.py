"""Organization ORM models: Organization, OrganizationMembership, OrganizationInvitation.

Design principles (Phase 3):
- Tenancy unit is `organization`. Every tenant-owned table (starting with
  `organization_memberships` here, and every domain table from Phase 4
  onward) carries an `organization_id` foreign key.
- Org-scoped role assignments do NOT live on a new table here — they reuse
  `app.rbac.models.UserRole.organization_id`, which already exists and is
  now FK-constrained to `organizations.id` (see migration 0003). This table
  only tracks membership *status*, independent of which roles a member
  holds.
- OrganizationInvitation follows the same raw-token-never-stored pattern as
  PasswordResetToken: only the SHA-256 hash is persisted.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrganizationStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DISABLED = "DISABLED"


class MembershipStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INVITED = "INVITED"
    SUSPENDED = "SUSPENDED"
    REMOVED = "REMOVED"


class InvitationStatus(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tenant. Every organization-scoped resource ultimately traces back here."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(220),
        unique=True,
        nullable=False,
        index=True,
        comment="Normalized, URL-safe, unique identifier derived from name.",
    )
    status: Mapped[OrganizationStatus] = mapped_column(
        Enum(OrganizationStatus, name="organization_status"),
        nullable=False,
        default=OrganizationStatus.ACTIVE,
        index=True,
    )

    memberships: Mapped[list[OrganizationMembership]] = relationship(
        "OrganizationMembership", back_populates="organization", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Organization slug={self.slug!r} status={self.status}>"


class OrganizationMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A user's membership in an organization, independent of their org-scoped roles.

    Roles held within this organization are looked up via
    `UserRole.organization_id == organization_id AND UserRole.user_id == user_id`
    — not stored on this row. This row only tracks whether the user currently
    has access to the organization at all (ACTIVE), is mid-invitation
    (INVITED), temporarily blocked (SUSPENDED), or has left/been removed
    (REMOVED, kept for audit history rather than hard-deleted).
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status"),
        nullable=False,
        default=MembershipStatus.ACTIVE,
        index=True,
    )

    organization: Mapped[Organization] = relationship(
        "Organization", back_populates="memberships", lazy="noload"
    )
    user: Mapped[User] = relationship("User", lazy="noload")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<OrganizationMembership org={self.organization_id} "
            f"user={self.user_id} status={self.status}>"
        )


class OrganizationInvitation(Base, UUIDPrimaryKeyMixin):
    """Single-use invitation for a person to join an organization with a given role.

    Only the SHA-256 hash of the raw invitation token is stored. The raw
    token is included in the invite link (emailed via EmailService) and
    never persisted, mirroring PasswordResetToken.
    """

    __tablename__ = "organization_invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        comment="Role to grant the invitee's membership upon acceptance.",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hex of raw invitation token. Raw token emailed, never stored.",
    )
    status: Mapped[InvitationStatus] = mapped_column(
        Enum(InvitationStatus, name="invitation_status"),
        nullable=False,
        default=InvitationStatus.PENDING,
        index=True,
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    organization: Mapped[Organization] = relationship("Organization", lazy="noload")
    role: Mapped[Role] = relationship("Role", lazy="noload")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<OrganizationInvitation org={self.organization_id} "
            f"email={self.email!r} status={self.status}>"
        )


# Avoid circular imports at module level.
from app.rbac.models import Role  # noqa: E402, F401
from app.users.models import User  # noqa: E402, F401
