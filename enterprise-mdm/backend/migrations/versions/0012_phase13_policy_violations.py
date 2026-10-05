"""Phase 13: advanced policy enforcement + automated remediation.

Revision ID: 0012_phase13_policy_violations
Revises: 0011_phase12_device_management
Create Date: 2026-08-25

Creates:
- policy_violations — one row per (organization, device, policy, rule)
  that has ever failed; the normalized, addressable, remediable
  counterpart to DeviceCompliance.details' free-form per-evaluation JSON.
  See app/compliance/models.py::PolicyViolation for the full design
  rationale (why this is a new table, not squeezed into
  DeviceCompliance/ComplianceResult).

Alters:
- audit_event_type enum: adds 5 Phase 13 event types (additive only,
  matching the 0003/.../0011 precedent).

No changes to DeviceCommand, BackgroundJob, or Policy/PolicyVersion
schemas — remediation reuses DeviceCommand and BackgroundJob entirely
unchanged; the new `remediation` policy-configuration section
(app/policies/schemas.py::RemediationPolicyConfig) lives inside
PolicyVersion.configuration's existing JSON blob, requiring no migration
of its own (the whole point of that column being JSON, per Phase 5's
original design).
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012_phase13_policy_violations"
down_revision = "0011_phase12_device_management"
branch_labels = None
depends_on = None

_NEW_AUDIT_EVENT_TYPES = [
    "COMPLIANCE_REMEDIATION_TRIGGERED",
    "COMPLIANCE_REMEDIATION_RETRIED",
    "COMPLIANCE_REMEDIATION_DISABLED",
    "COMPLIANCE_REMEDIATION_ENABLED",
    "COMPLIANCE_VIOLATION_RESOLVED",
]


def upgrade() -> None:
    # -- Additive enum extension -------------------------------------------
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    # -- New job type -------------------------------------------------------
    op.execute("ALTER TYPE background_job_type ADD VALUE IF NOT EXISTS 'COMPLIANCE_REMEDIATION'")

    # -- New enum -------------------------------------------------------
    policy_violation_status = postgresql.ENUM(
        "OPEN",
        "REMEDIATION_PENDING",
        "REMEDIATING",
        "RESOLVED",
        "REMEDIATION_FAILED",
        name="policy_violation_status",
    )
    policy_violation_status.create(op.get_bind(), checkfirst=True)
    policy_violation_status.create_type = False

    # -- policy_violations ----------------------------------------------
    op.create_table(
        "policy_violations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "policy_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_versions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rule_code", sa.String(100), nullable=False),
        sa.Column(
            "status",
            policy_violation_status,
            nullable=False,
            server_default="OPEN",
            index=True,
        ),
        sa.Column("expected_value", sa.String(255), nullable=True),
        sa.Column("current_value", sa.String(255), nullable=True),
        sa.Column("message", sa.String(500), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reason", sa.String(50), nullable=True),
        sa.Column(
            "remediation_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "last_remediation_command_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_commands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_remediation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "automated_remediation_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "organization_id",
            "device_id",
            "policy_id",
            "rule_code",
            name="uq_policy_violation_device_policy_rule",
        ),
    )
    op.create_index(
        "ix_policy_violations_org_status", "policy_violations", ["organization_id", "status"]
    )
    op.create_index(
        "ix_policy_violations_org_device", "policy_violations", ["organization_id", "device_id"]
    )


def downgrade() -> None:
    # Note: audit_event_type and background_job_type values added in
    # upgrade() are NOT removed — PostgreSQL has no ALTER TYPE ... DROP
    # VALUE, matching every prior phase's downgrade precedent.
    op.drop_index("ix_policy_violations_org_device", table_name="policy_violations")
    op.drop_index("ix_policy_violations_org_status", table_name="policy_violations")
    op.drop_table("policy_violations")

    bind = op.get_bind()
    postgresql.ENUM(name="policy_violation_status").drop(bind, checkfirst=True)
