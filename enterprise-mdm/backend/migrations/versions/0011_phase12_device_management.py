"""Phase 12: production device management operations.

Revision ID: 0011_phase12_device_management
Revises: 0010_phase11_provider_enrollment
Create Date: 2026-08-25

Alters:
- device_command_type enum: adds UNENROLL (additive, matches the
  0003/0004/0005/0006/0009 precedent — `ALTER TYPE ... ADD VALUE IF NOT
  EXISTS`). Real mapping: Android's `enterprises.devices.delete` (see
  app/platform/android/{client,provider}.py); Apple has no supported
  mapping yet (see app/platform/apple/provider.py), documented rather than
  guessed.

No other schema changes — device management-state/provider visibility
(GET /devices) and command-capability discovery
(GET /devices/{id}/commands/capabilities) are both computed at read time
from data that already exists (ProviderDeviceLink), not new columns. See
docs/architecture/phase-12.md.
"""

from __future__ import annotations

import alembic.op as op

# revision identifiers, used by Alembic.
revision = "0011_phase12_device_management"
down_revision = "0010_phase11_provider_enrollment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE device_command_type ADD VALUE IF NOT EXISTS 'UNENROLL'")


def downgrade() -> None:
    # PostgreSQL has no ALTER TYPE ... DROP VALUE — matching every prior
    # phase's downgrade precedent (0003 §, 0009, 0010), the added enum
    # value is not removed.
    pass
