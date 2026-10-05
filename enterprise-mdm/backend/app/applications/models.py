"""Application ORM models (Phase 14): catalog, assignment, and deployment.

Design mirrors two already-established patterns rather than inventing new
ones:

- `ApplicationAssignment` is `PolicyAssignment`'s polymorphic scope shape,
  reused directly — `scope_type` is `app.policies.models.PolicyScopeType`
  (ORGANIZATION/USER/DEVICE_GROUP/DEVICE), not a duplicate enum, since the
  semantics ("which devices does this apply to") are identical and a
  second near-copy of the same four values would only be a maintenance
  trap. See docs/architecture/phase-14.md for why this is a deliberate
  reuse, not an oversight.

- `ApplicationDeployment` is one row per (organization, device,
  application) — the same "current, addressable, re-opened-on-recurrence"
  shape as `app.compliance.models.PolicyViolation`, for the same reason:
  DeviceCommand rows are the append-only history (a new DeviceCommand per
  deployment attempt, never mutated), and this row is the queryable
  "what's the current state of this (device, app) pair" anchor. See
  app/applications/service.py for how a deployment's status is advanced
  in lockstep with its underlying DeviceCommand.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.devices.enums import DevicePlatform
from app.policies.models import PolicyScopeType

ApplicationScopeType = PolicyScopeType


class ApplicationRequirement(str, enum.Enum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class ApplicationDeploymentStatus(str, enum.Enum):
    """PENDING  -> INSTALLING (a DeviceCommand was dispatched)
    INSTALLING -> INSTALLED  (the command reached COMPLETED)
    INSTALLING -> FAILED     (the command reached FAILED/CANCELLED/EXPIRED)
    INSTALLED  -> REMOVED    (a subsequent UNINSTALL_APPLICATION command
                               reached COMPLETED)
    FAILED     -> INSTALLING (a new deploy attempt — see
                               ApplicationService.deploy_to_device)
    Reopened, never duplicated: like PolicyViolation, one row per
    (organization, device, application) — a re-deploy after FAILED/REMOVED
    updates this same row rather than inserting a second one, so "current
    status" is always an unambiguous single lookup; full attempt-by-attempt
    history lives in the DeviceCommand rows themselves (see
    ApplicationDeployment.last_command_id and
    GET /devices/{id}/commands for that trail)."""

    PENDING = "PENDING"
    INSTALLING = "INSTALLING"
    INSTALLED = "INSTALLED"
    FAILED = "FAILED"
    REMOVED = "REMOVED"


class Application(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One catalog entry. Platform-specific identifier fields are kept
    isolated per brief §2 rather than a single generic `identifier` column:
    `package_name` is populated (and `bundle_identifier` NULL) for
    `platform=ANDROID`; `bundle_identifier` is populated (and `package_name`
    NULL) for `platform in (IOS, IPADOS)`. Enforced by a CHECK constraint
    here (defense in depth) and validated again in
    ApplicationService.create_application/update_application (the actual
    422-producing check — the CHECK constraint exists only to make an
    invalid row impossible even from a future code path that forgets to
    call the service)."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "platform",
            "package_name",
            name="uq_application_org_platform_package",
        ),
        UniqueConstraint(
            "organization_id",
            "platform",
            "bundle_identifier",
            name="uq_application_org_platform_bundle",
        ),
        CheckConstraint(
            "(platform = 'ANDROID' AND package_name IS NOT NULL AND bundle_identifier IS NULL) "
            "OR (platform IN ('IOS', 'IPADOS') AND bundle_identifier IS NOT NULL "
            "AND package_name IS NULL) "
            "OR (platform = 'OTHER')",
            name="ck_application_platform_identifier",
        ),
        Index("ix_applications_org_platform", "organization_id", "platform"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[DevicePlatform] = mapped_column(
        Enum(DevicePlatform, name="device_platform"), nullable=False, index=True
    )
    package_name: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Android package identifier, e.g. com.example.app."
    )
    bundle_identifier: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Apple bundle identifier, e.g. com.example.App."
    )
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    managed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="Whether this codebase's own deployment/removal commands apply to this "
        "app. False marks a catalog entry as reference/inventory-only.",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    organization = relationship("Organization", lazy="noload")

    def __repr__(self) -> str:
        return f"<Application id={self.id} org={self.organization_id} name={self.name!r}>"


class ApplicationAssignment(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "application_assignments"
    __table_args__ = (
        UniqueConstraint(
            "application_id", "scope_type", "scope_id", name="uq_application_assignment_scope"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
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
        comment="organization_id / user_id / device_group_id / device_id depending on "
        "scope_type — same polymorphic-by-service-validation shape as "
        "PolicyAssignment.scope_id.",
    )
    requirement: Mapped[ApplicationRequirement] = mapped_column(
        Enum(ApplicationRequirement, name="application_requirement"),
        nullable=False,
        default=ApplicationRequirement.OPTIONAL,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    application = relationship("Application", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<ApplicationAssignment app={self.application_id} "
            f"{self.scope_type}={self.scope_id} requirement={self.requirement}>"
        )


class ApplicationDeployment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "application_deployments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "device_id",
            "application_id",
            name="uq_application_deployment_device_app",
        ),
        Index("ix_application_deployments_org_device", "organization_id", "device_id"),
        Index("ix_application_deployments_org_app", "organization_id", "application_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("application_assignments.id", ondelete="SET NULL"),
        nullable=True,
        comment="The assignment that triggered the most recent deployment attempt. "
        "May go stale (assignment later removed) without affecting this row's own "
        "history — informational traceability only, like PolicyViolation.policy_version_id.",
    )
    requirement: Mapped[ApplicationRequirement] = mapped_column(
        Enum(ApplicationRequirement, name="application_requirement"),
        nullable=False,
        comment="Snapshot of the triggering assignment's requirement at deployment time.",
    )
    status: Mapped[ApplicationDeploymentStatus] = mapped_column(
        Enum(ApplicationDeploymentStatus, name="application_deployment_status"),
        nullable=False,
        default=ApplicationDeploymentStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_command_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_commands.id", ondelete="SET NULL"),
        nullable=True,
        comment="The most recent DeviceCommand dispatched for this deployment — reused "
        "to check whether it reached a terminal state, exactly like "
        "PolicyViolation.last_remediation_command_id.",
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", lazy="noload")
    device = relationship("Device", lazy="noload")
    application = relationship("Application", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<ApplicationDeployment app={self.application_id} device={self.device_id} "
            f"status={self.status}>"
        )
