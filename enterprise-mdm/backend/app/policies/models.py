"""Policy ORM models: Policy, PolicyVersion, PolicyAssignment.

Design (STEP 6/8): a policy's `configuration` is a single JSON blob per
version, not a column per control — this is what makes "clean, extensible,
don't hardcode every OS-specific setting" achievable. See
app/policies/schemas.py for the typed Pydantic shape of that JSON
(password / device_security / application / network / compliance
sub-objects) and docs/architecture/enrollment-and-policy.md for the
category catalogue.

Versioning: `Policy` is the mutable "current pointer" row (name,
description, status, `current_version_id`). `PolicyVersion` rows are
immutable once created — editing a policy always creates a new
PolicyVersion and repoints `current_version_id`; a past version's
`configuration` is never mutated. This mirrors OrganizationInvitation's
raw-value-never-mutated pattern applied to full config history instead of
a token.

`Policy.current_version_id` and `PolicyVersion.policy_id` are a genuine
circular FK pair — the migration creates both tables with
`current_version_id` nullable and adds that FK via a separate
`ALTER TABLE` after `policy_versions` exists (same technique used for
`Device.enrollment_id` <-> `DeviceEnrollment.device_id`).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class PolicyStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class PolicyScopeType(str, enum.Enum):
    ORGANIZATION = "ORGANIZATION"
    USER = "USER"
    DEVICE_GROUP = "DEVICE_GROUP"
    DEVICE = "DEVICE"


class Policy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_policy_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PolicyStatus] = mapped_column(
        Enum(PolicyStatus, name="policy_status"),
        nullable=False,
        default=PolicyStatus.DRAFT,
        index=True,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "policy_versions.id",
            ondelete="SET NULL",
            # policies <-> policy_versions is a genuine circular FK
            # (policy_versions.policy_id -> policies.id, NOT NULL, is the
            # other half). See the matching note on Device.enrollment_id.
            use_alter=True,
            name="fk_policies_current_version_id",
        ),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization = relationship("Organization", lazy="noload")
    current_version = relationship(
        "PolicyVersion", foreign_keys=[current_version_id], lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<Policy id={self.id} org={self.organization_id} name={self.name!r}>"


class PolicyVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "policy_versions"
    __table_args__ = (UniqueConstraint("policy_id", "version", name="uq_policy_version"),)

    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(
        JSONType,
        nullable=False,
        comment="Category sub-objects (password/device_security/application/"
        "network/compliance). See app/policies/schemas.py for the typed shape.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        return f"<PolicyVersion policy={self.policy_id} v{self.version}>"


class PolicyAssignment(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "policy_assignments"
    __table_args__ = (
        UniqueConstraint(
            "policy_id", "scope_type", "scope_id", name="uq_policy_assignment_scope"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope_type: Mapped[PolicyScopeType] = mapped_column(
        Enum(PolicyScopeType, name="policy_scope_type"), nullable=False, index=True
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        comment="organization_id / user_id / device_group_id / device_id "
        "depending on scope_type. Polymorphic — validated against the "
        "correct table in the service layer, not a DB-level FK.",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    policy = relationship("Policy", lazy="noload")

    def __repr__(self) -> str:
        return f"<PolicyAssignment policy={self.policy_id} {self.scope_type}={self.scope_id}>"
