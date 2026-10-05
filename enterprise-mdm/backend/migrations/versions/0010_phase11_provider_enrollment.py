"""Phase 11: real provider-side enrollment provisioning.

Revision ID: 0010_phase11_provider_enrollment
Revises: 0009_phase10_provider_link_admin
Create Date: 2026-08-25

Alters:
- enrollment_tokens: adds `provision_via_provider` (bool, default false),
  `provider_enrollment_reference`, and `provider_enrollment_expires_at` —
  see app/enrollment/models.py::EnrollmentToken for what each column means
  and docs/architecture/phase-11.md for how they let a real Android device
  auto-resolve to the right organization on its first provider check-in,
  with no admin database write required.

Does not touch 0007/0008/0009. No new audit_event_type or
security_event_type values are needed — Phase 11 reuses
DEVICE_ENROLLMENT_REGISTERED, PROVIDER_DEVICE_LINK_CREATED/DELETED (all
existing), and the SecurityEventType.DEVICE_ENROLLMENT_FAILED value that
has existed since Phase 4 but had no code path raising it until now.
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0010_phase11_provider_enrollment"
down_revision = "0009_phase10_provider_link_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enrollment_tokens",
        sa.Column(
            "provision_via_provider",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "enrollment_tokens",
        sa.Column("provider_enrollment_reference", sa.String(500), nullable=True),
    )
    op.add_column(
        "enrollment_tokens",
        sa.Column("provider_enrollment_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A first-time provider check-in resolves back to exactly one
    # EnrollmentToken via a point lookup on this column (see
    # EnrollmentTokenRepository.get_by_provider_enrollment_reference_unscoped)
    # — indexed the same way provider_device_links indexes its own lookup
    # columns.
    op.create_index(
        "ix_enrollment_tokens_provider_enrollment_reference",
        "enrollment_tokens",
        ["provider_enrollment_reference"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_enrollment_tokens_provider_enrollment_reference", table_name="enrollment_tokens"
    )
    op.drop_column("enrollment_tokens", "provider_enrollment_expires_at")
    op.drop_column("enrollment_tokens", "provider_enrollment_reference")
    op.drop_column("enrollment_tokens", "provision_via_provider")
