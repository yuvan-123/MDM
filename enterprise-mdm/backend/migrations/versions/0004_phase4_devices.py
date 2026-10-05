"""Phase 4 Device Inventory & Management Foundation.

Revision ID: 0004_phase4_devices
Revises: 0003_phase3_multi_tenancy
Create Date: 2026-08-21

Creates:
- devices (organization-scoped inventory record; see app/devices/models.py
  for why this is INVENTORY only, not real enrollment/management)

Extends (additive-only, matching the 0003 pattern — Postgres has no
ALTER TYPE ... DROP VALUE):
- audit_event_type: DEVICE_CREATED, DEVICE_UPDATED, DEVICE_ASSIGNED,
  DEVICE_UNASSIGNED, DEVICE_BLOCKED, DEVICE_UNBLOCKED, DEVICE_RETIRED,
  DEVICE_DELETED, DEVICE_SENSITIVE_DATA_VIEWED
- security_event_type: DEVICE_ENROLLMENT_FAILED, DEVICE_CHECKIN_FAILED,
  DEVICE_NON_COMPLIANT, DEVICE_COMPROMISED, DEVICE_LOST, DEVICE_BLOCKED
  (reserved for future automated detection — nothing raises these yet)
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004_phase4_devices"
down_revision = "0003_phase3_multi_tenancy"
branch_labels = None
depends_on = None


_NEW_AUDIT_EVENT_TYPES = [
    "DEVICE_CREATED",
    "DEVICE_UPDATED",
    "DEVICE_ASSIGNED",
    "DEVICE_UNASSIGNED",
    "DEVICE_BLOCKED",
    "DEVICE_UNBLOCKED",
    "DEVICE_RETIRED",
    "DEVICE_DELETED",
    "DEVICE_SENSITIVE_DATA_VIEWED",
]

_NEW_SECURITY_EVENT_TYPES = [
    "DEVICE_ENROLLMENT_FAILED",
    "DEVICE_CHECKIN_FAILED",
    "DEVICE_NON_COMPLIANT",
    "DEVICE_COMPROMISED",
    "DEVICE_LOST",
    "DEVICE_BLOCKED",
]


def upgrade() -> None:
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")
    for event_type in _NEW_SECURITY_EVENT_TYPES:
        op.execute(f"ALTER TYPE security_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    device_platform = postgresql.ENUM(
        "ANDROID", "IOS", "IPADOS", "OTHER", name="device_platform"
    )
    device_platform.create(op.get_bind(), checkfirst=True)
    device_platform.create_type = False

    device_type = postgresql.ENUM("PHONE", "TABLET", "OTHER", name="device_type")
    device_type.create(op.get_bind(), checkfirst=True)
    device_type.create_type = False

    ownership_type = postgresql.ENUM(
        "CORPORATE", "BYOD", "COPE", "COBO", name="ownership_type"
    )
    ownership_type.create(op.get_bind(), checkfirst=True)
    ownership_type.create_type = False

    device_status = postgresql.ENUM(
        "ACTIVE", "INACTIVE", "LOST", "BLOCKED", "RETIRED", name="device_status"
    )
    device_status.create(op.get_bind(), checkfirst=True)
    device_status.create_type = False

    enrollment_status = postgresql.ENUM(
        "NOT_ENROLLED", "PENDING", "ENROLLED", "FAILED", "REVOKED", name="enrollment_status"
    )
    enrollment_status.create(op.get_bind(), checkfirst=True)
    enrollment_status.create_type = False

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("device_identifier", sa.String(200), nullable=True),
        sa.Column("serial_number", sa.String(200), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("manufacturer", sa.String(100), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("model_identifier", sa.String(100), nullable=True),
        sa.Column("platform", device_platform, nullable=False, index=True),
        sa.Column("operating_system", sa.String(100), nullable=True),
        sa.Column("os_version", sa.String(50), nullable=True),
        sa.Column("security_patch_level", sa.String(50), nullable=True),
        sa.Column("device_type", device_type, nullable=False),
        sa.Column("ownership_type", ownership_type, nullable=False),
        sa.Column(
            "status", device_status, nullable=False, server_default="ACTIVE", index=True
        ),
        sa.Column(
            "enrollment_status",
            enrollment_status,
            nullable=False,
            server_default="NOT_ENROLLED",
            index=True,
        ),
        sa.Column(
            "assigned_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("imei_hash", sa.String(64), nullable=True),
        sa.Column("imei_last4", sa.String(4), nullable=True),
        sa.Column("wifi_mac_hash", sa.String(64), nullable=True),
        sa.Column("wifi_mac_last4", sa.String(4), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_in_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_enrollment_attempt_at", sa.DateTime(timezone=True), nullable=True),
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
            "organization_id", "device_identifier", name="uq_device_org_identifier"
        ),
        sa.UniqueConstraint("organization_id", "serial_number", name="uq_device_org_serial"),
        sa.UniqueConstraint("organization_id", "imei_hash", name="uq_device_org_imei_hash"),
    )

    op.create_index("ix_devices_org_status", "devices", ["organization_id", "status"])
    op.create_index("ix_devices_org_platform", "devices", ["organization_id", "platform"])
    op.create_index(
        "ix_devices_org_enrollment_status", "devices", ["organization_id", "enrollment_status"]
    )
    op.create_index(
        "ix_devices_org_assigned_user", "devices", ["organization_id", "assigned_user_id"]
    )
    op.create_index("ix_devices_org_created_at", "devices", ["organization_id", "created_at"])
    op.create_index("ix_devices_org_ownership", "devices", ["organization_id", "ownership_type"])


def downgrade() -> None:
    # Note: the audit_event_type / security_event_type values added in
    # upgrade() are NOT removed — PostgreSQL has no ALTER TYPE ... DROP
    # VALUE, matching the 0003 downgrade's documented limitation.
    op.drop_index("ix_devices_org_ownership", table_name="devices")
    op.drop_index("ix_devices_org_created_at", table_name="devices")
    op.drop_index("ix_devices_org_assigned_user", table_name="devices")
    op.drop_index("ix_devices_org_enrollment_status", table_name="devices")
    op.drop_index("ix_devices_org_platform", table_name="devices")
    op.drop_index("ix_devices_org_status", table_name="devices")

    op.drop_table("devices")

    bind = op.get_bind()
    postgresql.ENUM(name="enrollment_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="device_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="ownership_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="device_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="device_platform").drop(bind, checkfirst=True)
