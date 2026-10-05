"""Compliance ORM models: DeviceCompliance (current-state cache), ComplianceResult (history).

Same "current + history" split as Policy/PolicyVersion:
- DeviceCompliance: one row per device (upserted), the *latest* evaluation
  result — cheap to query for dashboards/filters without scanning history.
- ComplianceResult: insert-only, one row per evaluation run, for
  auditability (STEP 9: "Keep auditability").
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class ComplianceStatus(str, enum.Enum):
    COMPLIANT = "COMPLIANT"
    NON_COMPLIANT = "NON_COMPLIANT"
    UNKNOWN = "UNKNOWN"


class DeviceCompliance(Base):
    __tablename__ = "device_compliance"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[ComplianceStatus] = mapped_column(
        Enum(ComplianceStatus, name="compliance_status"),
        nullable=False,
        default=ComplianceStatus.UNKNOWN,
        index=True,
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policies.id", ondelete="SET NULL"), nullable=True
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True
    )
    details: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    device = relationship("Device", lazy="noload")

    def __repr__(self) -> str:
        return f"<DeviceCompliance device={self.device_id} status={self.status}>"


class ComplianceResult(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "compliance_results"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policies.id", ondelete="SET NULL"), nullable=True
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[ComplianceStatus] = mapped_column(
        Enum(ComplianceStatus, name="compliance_status"), nullable=False, index=True
    )
    details: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONType, nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL = automatic/system-triggered evaluation (e.g. from a heartbeat).",
    )

    def __repr__(self) -> str:
        return f"<ComplianceResult device={self.device_id} status={self.status}>"


class ViolationStatus(str, enum.Enum):
    """OPEN -> REMEDIATION_PENDING (a COMPLIANCE_REMEDIATION job enqueued)
        -> REMEDIATING (the job created a DeviceCommand)
        -> OPEN (the command reached a non-COMPLETED terminal state — the
            device is still failing the rule, eligible for another attempt
            next evaluation, subject to remediation_attempts/max_attempts)
        -> RESOLVED (the rule now passes, or the policy/assignment that
            produced this violation no longer applies)
        -> REMEDIATION_FAILED (remediation_attempts reached the policy's
            max_attempts with no successful resolution — requires an admin:
            manual retry, resolve, or disable automated remediation)
    RESOLVED is not always terminal: if the same (device, policy, rule)
    fails again later, app/compliance/service.py reopens it fresh (a new
    detection, remediation_attempts reset) rather than leaving history on
    a stale row — see that module's own docstring."""

    OPEN = "OPEN"
    REMEDIATION_PENDING = "REMEDIATION_PENDING"
    REMEDIATING = "REMEDIATING"
    RESOLVED = "RESOLVED"
    REMEDIATION_FAILED = "REMEDIATION_FAILED"


class PolicyViolation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Phase 13: one row per (organization, device, policy, rule) that has
    ever failed — the normalized, addressable, remediable counterpart to
    DeviceCompliance.details' free-form per-evaluation JSON snapshot.
    Deliberately a new table, not squeezed into DeviceCompliance/
    ComplianceResult: neither of those tracks one specific rule failure as
    its own entity with its own lifecycle and remediation history across
    many evaluations — DeviceCompliance is overwritten every evaluation,
    and ComplianceResult is pure insert-only history, neither offers
    anything to attach "this is still the same open problem" identity to.

    Uniqueness is scoped to (organization, device, policy, rule_code) —
    NOT filtered to only-open rows — so a re-detection after RESOLVED
    reuses (updates) the same row rather than accumulating duplicate
    history for what is, from an addressing standpoint, the same
    violation recurring."""

    __tablename__ = "policy_violations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "device_id",
            "policy_id",
            "rule_code",
            name="uq_policy_violation_device_policy_rule",
        ),
        Index("ix_policy_violations_org_status", "organization_id", "status"),
        Index("ix_policy_violations_org_device", "organization_id", "device_id"),
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
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("policy_versions.id", ondelete="SET NULL"),
        nullable=True,
        comment="Which version most recently detected this violation — "
        "informational only, re-evaluation always uses the policy's "
        "CURRENT version, not this snapshot.",
    )
    rule_code: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment='e.g. "MINIMUM_OS_VERSION" — see app/compliance/rules.py.',
    )
    status: Mapped[ViolationStatus] = mapped_column(
        Enum(ViolationStatus, name="policy_violation_status"),
        nullable=False,
        default=ViolationStatus.OPEN,
        index=True,
    )
    expected_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        comment="Latest RuleResult.message at detection/re-detection time.",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Reset on reopen (a fresh occurrence after RESOLVED) — never advanced by a "
        "mere re-detection of an already-open violation; see last_seen_at for that.",
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Updated on every evaluation that still finds this rule failing, "
        "whether or not detected_at changes.",
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        comment='"RULE_PASSED" | "POLICY_UNASSIGNED" | "POLICY_CHANGED" | "MANUALLY_RESOLVED" — '
        "free string, not a DB enum, matching DeviceCommand.error_code's own precedent "
        "for a small closed-in-practice-but-not-DB-enforced vocabulary.",
    )
    remediation_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_remediation_command_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_commands.id", ondelete="SET NULL"),
        nullable=True,
        comment="The most recent DeviceCommand created to remediate this violation — "
        "reused by app/compliance/service.py to check whether it reached a terminal "
        "state before deciding whether another attempt is warranted. Never a second "
        "command table — see app/commands/models.py::DeviceCommand.",
    )
    last_remediation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    automated_remediation_disabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="Admin override (POST .../disable-remediation) — this specific "
        "violation is excluded from automatic remediation even if the policy's "
        "own remediation.mode is COMMAND. The policy configuration itself is unchanged.",
    )

    organization = relationship("Organization", lazy="noload")
    device = relationship("Device", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<PolicyViolation id={self.id} device={self.device_id} "
            f"rule={self.rule_code} status={self.status}>"
        )
