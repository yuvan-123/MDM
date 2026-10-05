"""Phase 7 background jobs, command reconciliation, and provider events.

Revision ID: 0007_phase7_background_jobs
Revises: 0006_phase6_device_commands
Create Date: 2026-08-23

Creates:
- background_jobs
- provider_events

No audit_event_type changes this phase — reconciliation reuses the
existing DEVICE_COMMAND_EXPIRED type and scheduled compliance evaluations
reuse the existing COMPLIANCE_EVALUATED type (both from earlier phases).

Also fixes an independent, pre-existing gap in migrations/env.py: it was
missing `import app.commands.models` (Phase 6), meaning `device_commands`
was invisible to Base.metadata/autogenerate since 0006 — see that file's
updated comment.
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0007_phase7_background_jobs"
down_revision = "0006_phase6_device_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- New enums ------------------------------------------------------
    background_job_type = postgresql.ENUM(
        "COMPLIANCE_EVALUATION", "POLICY_REEVALUATION_FANOUT", name="background_job_type"
    )
    background_job_type.create(op.get_bind(), checkfirst=True)
    background_job_type.create_type = False

    background_job_status = postgresql.ENUM(
        "PENDING", "CLAIMED", "COMPLETED", "FAILED", name="background_job_status"
    )
    background_job_status.create(op.get_bind(), checkfirst=True)
    background_job_status.create_type = False

    # -- background_jobs --------------------------------------------------
    op.create_table(
        "background_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True,
        ),
        sa.Column("job_type", background_job_type, nullable=False, index=True),
        sa.Column(
            "status", background_job_status, nullable=False, server_default="PENDING", index=True
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("dedup_key", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index(
        "ix_background_jobs_status_scheduled", "background_jobs", ["status", "scheduled_at"]
    )
    op.create_index(
        "ix_background_jobs_org_type", "background_jobs", ["organization_id", "job_type"]
    )
    op.create_index(
        "ix_background_jobs_dedup",
        "background_jobs",
        ["organization_id", "job_type", "dedup_key"],
    )

    # -- provider_events ----------------------------------------------------
    op.create_table(
        "provider_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True,
        ),
        sa.Column(
            "device_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("provider_name", sa.String(50), nullable=False, index=True),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider_name", "provider_event_id", name="uq_provider_event_dedupe"
        ),
    )


def downgrade() -> None:
    op.drop_table("provider_events")

    op.drop_index("ix_background_jobs_dedup", table_name="background_jobs")
    op.drop_index("ix_background_jobs_org_type", table_name="background_jobs")
    op.drop_index("ix_background_jobs_status_scheduled", table_name="background_jobs")
    op.drop_table("background_jobs")

    bind = op.get_bind()
    postgresql.ENUM(name="background_job_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="background_job_type").drop(bind, checkfirst=True)
