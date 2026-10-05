"""Phase 10: Apple identity separation, provider-device-link
administration, and job/event manual-retry audit events.

Revision ID: 0009_phase10_provider_link_admin
Revises: 0008_phase8_command_dispatch_and_provider_links
Create Date: 2026-08-24

Alters:
- provider_device_links: adds `apple_push_token` (Apple rows only — see
  app/provider_events/models.py::ProviderDeviceLink for why one string
  column was never enough for Apple specifically) and `link_source` (new
  enum, MANUAL/PROVIDER_ENROLLED, defaults every existing row to MANUAL —
  correct for every row that has ever existed, since nothing in this
  codebase has ever created one any other way).
- audit_event_type enum: adds 5 Phase 10 event types (additive only,
  matching the 0003/0004/0005/0006 precedent).

## Data migration note
Phase 9's Apple check-in handler had a real bug: its TokenUpdate handling
overwrote `provider_device_id` (meant to hold the UDID) with the device's
push token, corrupting the UDID-based resolution key after the first
token update. No live deployment of this codebase has ever existed (no
Docker/Postgres available in any development session to date), so no real
corrupted row is known to exist — but if one does, this migration cannot
distinguish "provider_device_id currently holds a UDID" from "it currently
holds a push-token hex string left over from the Phase 9 bug." Any
existing Apple-provider row should be manually reviewed and its
`provider_device_id` reset to the device's real UDID (with the push token,
if known, moved into the new `apple_push_token` column) before relying on
it — this migration only adds the columns; it does not attempt to guess
which existing value is which.
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009_phase10_provider_link_admin"
down_revision = "0008_phase8_command_dispatch_and_provider_links"
branch_labels = None
depends_on = None

_NEW_AUDIT_EVENT_TYPES = [
    "PROVIDER_DEVICE_LINK_CREATED",
    "PROVIDER_DEVICE_LINK_UPDATED",
    "PROVIDER_DEVICE_LINK_DELETED",
    "BACKGROUND_JOB_RETRIED",
    "PROVIDER_EVENT_RETRIED",
]


def upgrade() -> None:
    # -- Additive enum extension -------------------------------------------
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    # -- New enum -------------------------------------------------------
    provider_device_link_source = postgresql.ENUM(
        "MANUAL", "PROVIDER_ENROLLED", name="provider_device_link_source"
    )
    provider_device_link_source.create(op.get_bind(), checkfirst=True)
    provider_device_link_source.create_type = False

    # -- provider_device_links: Apple identity separation --------------------
    op.add_column(
        "provider_device_links", sa.Column("apple_push_token", sa.String(255), nullable=True)
    )
    op.add_column(
        "provider_device_links",
        sa.Column(
            "link_source",
            provider_device_link_source,
            nullable=False,
            server_default="MANUAL",
        ),
    )


def downgrade() -> None:
    # Note: audit_event_type values added in upgrade() are NOT removed —
    # PostgreSQL has no ALTER TYPE ... DROP VALUE, matching every prior
    # phase's downgrade precedent.
    op.drop_column("provider_device_links", "link_source")
    op.drop_column("provider_device_links", "apple_push_token")

    bind = op.get_bind()
    postgresql.ENUM(name="provider_device_link_source").drop(bind, checkfirst=True)
