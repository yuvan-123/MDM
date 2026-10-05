"""Phase 5 Device Enrollment, Lifecycle, and Policy Management.

Revision ID: 0005_phase5_enrollment_policy
Revises: 0004_phase4_devices
Create Date: 2026-08-21

Creates:
- enrollment_tokens, device_enrollments
- device_groups, device_group_members
- policies, policy_versions, policy_assignments
- device_compliance, compliance_results

Alters:
- devices: adds enrollment_id (FK to device_enrollments, added after that
  table exists — see note below), device_secret_hash, client_version,
  is_encrypted, has_screen_lock, is_rooted
- enrollment_status enum: adds APPROVED, REJECTED, EXPIRED (additive only,
  matching the 0003/0004 precedent)
- audit_event_type enum: adds 17 Phase 5 event types (additive only)

Circular FK note: `devices.enrollment_id` -> `device_enrollments.id` and
`device_enrollments.device_id` -> `devices.id` reference each other.
`device_enrollments.device_id` is created NOT NULL as part of that table's
initial CREATE TABLE (devices already exists, from 0004); `devices.enrollment_id`
is added afterward via ALTER TABLE, nullable, once device_enrollments exists.
The exact same technique is used for `policies.current_version_id` <->
`policy_versions.policy_id`.
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005_phase5_enrollment_policy"
down_revision = "0004_phase4_devices"
branch_labels = None
depends_on = None


_NEW_ENROLLMENT_STATUS_VALUES = ["APPROVED", "REJECTED", "EXPIRED"]

_NEW_AUDIT_EVENT_TYPES = [
    "ENROLLMENT_TOKEN_CREATED",
    "ENROLLMENT_TOKEN_REVOKED",
    "DEVICE_ENROLLMENT_REGISTERED",
    "DEVICE_ENROLLMENT_APPROVED",
    "DEVICE_ENROLLMENT_REJECTED",
    "DEVICE_ENROLLMENT_REVOKED",
    "DEVICE_GROUP_CREATED",
    "DEVICE_GROUP_UPDATED",
    "DEVICE_GROUP_DELETED",
    "DEVICE_GROUP_MEMBER_ADDED",
    "DEVICE_GROUP_MEMBER_REMOVED",
    "POLICY_CREATED",
    "POLICY_UPDATED",
    "POLICY_DELETED",
    "POLICY_ASSIGNED",
    "POLICY_UNASSIGNED",
    "COMPLIANCE_EVALUATED",
]


def upgrade() -> None:
    # -- Additive enum extensions ------------------------------------------------
    for value in _NEW_ENROLLMENT_STATUS_VALUES:
        op.execute(f"ALTER TYPE enrollment_status ADD VALUE IF NOT EXISTS '{value}'")
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    # -- New enums -----------------------------------------------------------
    enrollment_token_status = postgresql.ENUM(
        "ACTIVE", "REVOKED", "EXPIRED", "EXHAUSTED", name="enrollment_token_status"
    )
    enrollment_token_status.create(op.get_bind(), checkfirst=True)
    enrollment_token_status.create_type = False

    device_enrollment_status = postgresql.ENUM(
        "PENDING", "APPROVED", "ENROLLED", "REJECTED", "REVOKED", "EXPIRED",
        name="device_enrollment_status",
    )
    device_enrollment_status.create(op.get_bind(), checkfirst=True)
    device_enrollment_status.create_type = False

    policy_status = postgresql.ENUM("DRAFT", "ACTIVE", "ARCHIVED", name="policy_status")
    policy_status.create(op.get_bind(), checkfirst=True)
    policy_status.create_type = False

    policy_scope_type = postgresql.ENUM(
        "ORGANIZATION", "USER", "DEVICE_GROUP", "DEVICE", name="policy_scope_type"
    )
    policy_scope_type.create(op.get_bind(), checkfirst=True)
    policy_scope_type.create_type = False

    compliance_status = postgresql.ENUM(
        "COMPLIANT", "NON_COMPLIANT", "UNKNOWN", name="compliance_status"
    )
    compliance_status.create(op.get_bind(), checkfirst=True)
    compliance_status.create_type = False

    # device_platform / device_type already exist (created in 0004) — reference
    # without re-creating.
    device_platform_ref = postgresql.ENUM(
        "ANDROID", "IOS", "IPADOS", "OTHER", name="device_platform", create_type=False
    )
    device_type_ref = postgresql.ENUM(
        "PHONE", "TABLET", "OTHER", name="device_type", create_type=False
    )

    # -- 1. enrollment_tokens -------------------------------------------------
    op.create_table(
        "enrollment_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("label", sa.String(200), nullable=True),
        sa.Column(
            "status", enrollment_token_status, nullable=False, server_default="ACTIVE", index=True
        ),
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "target_user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("allowed_platform", device_platform_ref, nullable=True),
        sa.Column("allowed_device_type", device_type_ref, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    # -- 2. device_enrollments -------------------------------------------------
    op.create_table(
        "device_enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "enrollment_token_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("enrollment_tokens.id", ondelete="SET NULL"), nullable=True, index=True,
        ),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True, index=True,
        ),
        sa.Column(
            "status", device_enrollment_status, nullable=False, server_default="PENDING", index=True
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rejected_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "revoked_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    # -- 3. devices: new columns (Phase 5) --------------------------------------
    op.add_column(
        "devices",
        sa.Column(
            "enrollment_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_enrollments.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index("ix_devices_enrollment_id", "devices", ["enrollment_id"])
    op.add_column("devices", sa.Column("device_secret_hash", sa.String(64), nullable=True))
    op.add_column("devices", sa.Column("client_version", sa.String(50), nullable=True))
    op.add_column("devices", sa.Column("is_encrypted", sa.Boolean(), nullable=True))
    op.add_column("devices", sa.Column("has_screen_lock", sa.Boolean(), nullable=True))
    op.add_column("devices", sa.Column("is_rooted", sa.Boolean(), nullable=True))

    # -- 4. device_groups / device_group_members --------------------------------
    op.create_table(
        "device_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "name", name="uq_device_group_org_name"),
    )

    op.create_table(
        "device_group_members",
        sa.Column(
            "group_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_groups.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )

    # -- 5. policies / policy_versions / policy_assignments ---------------------
    op.create_table(
        "policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", policy_status, nullable=False, server_default="DRAFT", index=True),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "updated_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "name", name="uq_policy_org_name"),
    )

    op.create_table(
        "policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "policy_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("configuration", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.UniqueConstraint("policy_id", "version", name="uq_policy_version"),
    )

    op.create_foreign_key(
        "fk_policies_current_version_id",
        "policies",
        "policy_versions",
        ["current_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "policy_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "policy_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policies.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("scope_type", policy_scope_type, nullable=False, index=True),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.UniqueConstraint(
            "policy_id", "scope_type", "scope_id", name="uq_policy_assignment_scope"
        ),
    )
    op.create_index(
        "ix_policy_assignments_org_scope",
        "policy_assignments",
        ["organization_id", "scope_type", "scope_id"],
    )

    # -- 6. device_compliance / compliance_results ------------------------------
    op.create_table(
        "device_compliance",
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "status", compliance_status, nullable=False, server_default="UNKNOWN", index=True
        ),
        sa.Column(
            "policy_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policies.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "policy_version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "compliance_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "policy_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policies.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "policy_version_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("status", compliance_status, nullable=False, index=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evaluated_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    # Note: enrollment_status / audit_event_type values added in upgrade()
    # are NOT removed — PostgreSQL has no ALTER TYPE ... DROP VALUE,
    # matching the 0003/0004 downgrade precedent.
    op.drop_table("compliance_results")
    op.drop_table("device_compliance")

    op.drop_index("ix_policy_assignments_org_scope", table_name="policy_assignments")
    op.drop_table("policy_assignments")

    op.drop_constraint("fk_policies_current_version_id", "policies", type_="foreignkey")
    op.drop_table("policy_versions")
    op.drop_table("policies")

    op.drop_table("device_group_members")
    op.drop_table("device_groups")

    op.drop_column("devices", "is_rooted")
    op.drop_column("devices", "has_screen_lock")
    op.drop_column("devices", "is_encrypted")
    op.drop_column("devices", "client_version")
    op.drop_column("devices", "device_secret_hash")
    op.drop_index("ix_devices_enrollment_id", table_name="devices")
    op.drop_column("devices", "enrollment_id")

    op.drop_table("device_enrollments")
    op.drop_table("enrollment_tokens")

    bind = op.get_bind()
    postgresql.ENUM(name="compliance_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="policy_scope_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="policy_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="device_enrollment_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="enrollment_token_status").drop(bind, checkfirst=True)
