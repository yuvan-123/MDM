"""RBAC ORM models: Role, Permission, RolePermission (join), UserRole (join).

Design principles:
- Roles and permissions are DATA, not code enums. This means an operator
  can add a custom role (e.g. "TIER2_SUPPORT") without a deployment.
- Permission strings follow resource.action (e.g. "device.read").
- UserRole.organization_id is nullable: NULL = platform-level role (e.g.
  SUPER_ADMIN), non-NULL = org-scoped role (populated in Phase 3 when
  the organizations table exists).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Role(Base, UUIDPrimaryKeyMixin):
    """A named collection of permissions.

    System roles (is_system_role=True) are seeded by migrations and must
    not be deleted by operators via the API; only custom roles may be
    removed.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_system_role: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    role_permissions: Mapped[list[RolePermission]] = relationship(
        "RolePermission", back_populates="role", lazy="noload"
    )
    user_roles: Mapped[list[UserRole]] = relationship(
        "UserRole", back_populates="role", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Role name={self.name!r}>"


class Permission(Base, UUIDPrimaryKeyMixin):
    """A single capability string such as 'device.read' or 'device.wipe'.

    Permissions are seeded by migrations; application code references them
    by their `name` string constant only (never by primary key).
    """

    __tablename__ = "permissions"

    name: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(300), nullable=False)
    resource: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)

    role_permissions: Mapped[list[RolePermission]] = relationship(
        "RolePermission", back_populates="permission", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Permission name={self.name!r}>"


class RolePermission(Base):
    """Many-to-many join between Role and Permission."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role: Mapped[Role] = relationship("Role", back_populates="role_permissions")
    permission: Mapped[Permission] = relationship(
        "Permission", back_populates="role_permissions"
    )


class UserRole(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Assignment of a role to a user, optionally scoped to an organization.

    Phase 2: organization_id is always NULL (platform-level assignments).
    Phase 3: The organizations table will be created and this FK will be
             populated for org-scoped roles. A separate check will enforce
             that SUPER_ADMIN always has organization_id=NULL.

    granted_by is nullable to handle seed/bootstrap scenarios where no
    actor user exists yet.
    """

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "role_id",
            "organization_id",
            name="uq_user_role_org",
            postgresql_nulls_not_distinct=True,
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL = platform-level (SUPER_ADMIN, etc.)
    # Non-null = org-scoped role (Phase 3 populates this)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="NULL for platform-level roles; organization FK added in Phase 3.",
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship(  # noqa: F821
        "User", foreign_keys=[user_id], back_populates="user_roles"
    )
    role: Mapped[Role] = relationship("Role", back_populates="user_roles")


# Avoid circular import at module level
from app.users.models import User  # noqa: E402, F401
