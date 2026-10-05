"""Phase 8: async command dispatch, provider-event processing, and
tenant-safe provider-device resolution.

Revision ID: 0008_phase8_command_dispatch_and_provider_links
Revises: 0007_phase7_background_jobs
Create Date: 2026-08-24

Creates:
- provider_device_links (explicit organization+provider+provider_device_id
  -> device mapping, superseding the Phase 7 approach of guessing via
  Device.device_identifier)

Alters:
- background_job_type enum: adds COMMAND_DISPATCH (additive, matches
  0006/0007 precedent)
- provider_events: adds processing_status (new enum), processed_at,
  processing_error

No audit_event_type changes — command-dispatch audit events reuse the
existing DEVICE_COMMAND_* types (Phase 6/7); provider-event processing is
deliberately not audited (see docs/architecture/provider-events.md §5,
matching the heartbeat precedent).
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_phase8_command_dispatch_and_provider_links"
down_revision = "0007_phase7_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Additive enum extension -------------------------------------------
    op.execute("ALTER TYPE background_job_type ADD VALUE IF NOT EXISTS 'COMMAND_DISPATCH'")

    # -- New enum -------------------------------------------------------
    provider_event_processing_status = postgresql.ENUM(
        "PENDING", "PROCESSED", "FAILED", "IGNORED", name="provider_event_processing_status"
    )
    provider_event_processing_status.create(op.get_bind(), checkfirst=True)
    provider_event_processing_status.create_type = False

    # -- provider_events: processing tracking --------------------------------
    op.add_column(
        "provider_events",
        sa.Column(
            "processing_status",
            provider_event_processing_status,
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "provider_events", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("provider_events", sa.Column("processing_error", sa.Text(), nullable=True))
    op.create_index(
        "ix_provider_events_processing_status", "provider_events", ["processing_status"]
    )

    # -- provider_device_links ------------------------------------------------
    op.create_table(
        "provider_device_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("provider_name", sa.String(50), nullable=False),
        sa.Column("provider_device_id", sa.String(255), nullable=False),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.UniqueConstraint(
            "organization_id", "provider_name", "provider_device_id",
            name="uq_provider_device_link",
        ),
    )
    op.create_index(
        "ix_provider_device_links_device", "provider_device_links", ["device_id"]
    )


def downgrade() -> None:
    # Note: the COMMAND_DISPATCH value added to background_job_type is NOT
    # removed — PostgreSQL has no ALTER TYPE ... DROP VALUE, matching every
    # prior phase's downgrade precedent.
    op.drop_index("ix_provider_device_links_device", table_name="provider_device_links")
    op.drop_table("provider_device_links")

    op.drop_index("ix_provider_events_processing_status", table_name="provider_events")
    op.drop_column("provider_events", "processing_error")
    op.drop_column("provider_events", "processed_at")
    op.drop_column("provider_events", "processing_status")

    bind = op.get_bind()
    postgresql.ENUM(name="provider_event_processing_status").drop(bind, checkfirst=True)
