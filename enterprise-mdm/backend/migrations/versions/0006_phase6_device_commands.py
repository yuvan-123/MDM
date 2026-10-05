"""Phase 6 Remote Device Command & Management.

Revision ID: 0006_phase6_device_commands
Revises: 0005_phase5_enrollment_policy
Create Date: 2026-08-21

Creates:
- device_commands

Alters:
- audit_event_type enum: adds 6 Phase 6 event types (additive only,
  matching the 0003/0004/0005 precedent)

No circular FK here — unlike 0005, device_commands only references
existing tables (organizations, devices, users), all created in earlier
migrations, so no delayed ALTER TABLE / use_alter technique is needed.
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_phase6_device_commands"
down_revision = "0005_phase5_enrollment_policy"
branch_labels = None
depends_on = None


_NEW_AUDIT_EVENT_TYPES = [
    "DEVICE_COMMAND_CREATED",
    "DEVICE_COMMAND_DISPATCHED",
    "DEVICE_COMMAND_COMPLETED",
    "DEVICE_COMMAND_FAILED",
    "DEVICE_COMMAND_CANCELLED",
    "DEVICE_COMMAND_EXPIRED",
]


def upgrade() -> None:
    # -- Additive enum extension -------------------------------------------------
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    # -- New enums ----------------------------------------------------------
    device_command_type = postgresql.ENUM(
        "LOCK", "REBOOT", "WIPE", "REFRESH_INFO", "POLICY_SYNC", name="device_command_type"
    )
    device_command_type.create(op.get_bind(), checkfirst=True)
    device_command_type.create_type = False

    device_command_status = postgresql.ENUM(
        "PENDING", "SENT", "COMPLETED", "FAILED", "CANCELLED", "EXPIRED",
        name="device_command_status",
    )
    device_command_status.create(op.get_bind(), checkfirst=True)
    device_command_status.create_type = False

    # -- device_commands ------------------------------------------------------
    op.create_table(
        "device_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("command_type", device_command_type, nullable=False, index=True),
        sa.Column(
            "status", device_command_status, nullable=False, server_default="PENDING", index=True
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("provider_command_id", sa.String(255), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "requested_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "cancelled_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint(
            "organization_id", "device_id", "idempotency_key",
            name="uq_device_command_org_device_idempotency",
        ),
    )
    op.create_index(
        "ix_device_commands_org_device", "device_commands", ["organization_id", "device_id"]
    )
    op.create_index(
        "ix_device_commands_org_status", "device_commands", ["organization_id", "status"]
    )


def downgrade() -> None:
    # Note: audit_event_type values added in upgrade() are NOT removed —
    # PostgreSQL has no ALTER TYPE ... DROP VALUE, matching the
    # 0003/0004/0005 downgrade precedent.
    op.drop_index("ix_device_commands_org_status", table_name="device_commands")
    op.drop_index("ix_device_commands_org_device", table_name="device_commands")
    op.drop_table("device_commands")

    bind = op.get_bind()
    postgresql.ENUM(name="device_command_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="device_command_type").drop(bind, checkfirst=True)
