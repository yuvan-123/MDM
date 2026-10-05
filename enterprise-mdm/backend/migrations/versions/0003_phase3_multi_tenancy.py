"""Phase 3 Multi-Tenancy and Organization Management.

Revision ID: 0003_phase3_multi_tenancy
Revises: 0002_phase2_auth_rbac
Create Date: 2026-08-20

Creates:
- organizations
- organization_memberships
- organization_invitations

Alters (columns already existed as nullable/unconstrained from Phase 2,
added here now that `organizations` exists as an FK target):
- user_roles.organization_id -> FK organizations.id ON DELETE CASCADE
- audit_logs.organization_id -> FK organizations.id ON DELETE SET NULL

Adds (new column, doesn't exist yet):
- security_events.organization_id (nullable) -> FK organizations.id ON DELETE SET NULL

Org-scoped role assignments are NOT a new table — they reuse
`user_roles.organization_id` (see docs/security/rbac.md and
docs/architecture/multi-tenancy.md for the design rationale).
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_phase3_multi_tenancy"
down_revision = "0002_phase2_auth_rbac"
branch_labels = None
depends_on = None


_NEW_AUDIT_EVENT_TYPES = [
    "ORGANIZATION_CREATED",
    "ORGANIZATION_UPDATED",
    "ORGANIZATION_SUSPENDED",
    "ORGANIZATION_DISABLED",
    "ORGANIZATION_ACCESS_DENIED",
    "MEMBER_INVITED",
    "MEMBER_ACCEPTED",
    "MEMBER_UPDATED",
    "MEMBER_REMOVED",
    "MEMBER_SUSPENDED",
]


def upgrade() -> None:
    # Extend the existing audit_event_type enum with Phase 3 event types.
    # Postgres allows ADD VALUE inside a transaction (PG 12+) as long as the
    # new value isn't used in the same transaction. There is no DROP VALUE —
    # downgrade() intentionally leaves these values in place (documented
    # there).
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    # Postgres ENUM types
    organization_status = postgresql.ENUM(
        "ACTIVE", "SUSPENDED", "DISABLED", name="organization_status"
    )
    organization_status.create(op.get_bind(), checkfirst=True)
    organization_status.create_type = False

    membership_status = postgresql.ENUM(
        "ACTIVE", "INVITED", "SUSPENDED", "REMOVED", name="membership_status"
    )
    membership_status.create(op.get_bind(), checkfirst=True)
    membership_status.create_type = False

    invitation_status = postgresql.ENUM(
        "PENDING", "ACCEPTED", "EXPIRED", "REVOKED", name="invitation_status"
    )
    invitation_status.create(op.get_bind(), checkfirst=True)
    invitation_status.create_type = False

    # 1. organizations
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(220), nullable=False, unique=True, index=True),
        sa.Column(
            "status",
            organization_status,
            nullable=False,
            server_default="ACTIVE",
            index=True,
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
    )

    # 2. organization_memberships
    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status",
            membership_status,
            nullable=False,
            server_default="ACTIVE",
            index=True,
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
        sa.UniqueConstraint("user_id", "organization_id", name="uq_membership_user_org"),
    )
    op.create_index(
        "ix_org_memberships_org_status",
        "organization_memberships",
        ["organization_id", "status"],
    )

    # 3. organization_invitations
    op.create_table(
        "organization_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "status",
            invitation_status,
            nullable=False,
            server_default="PENDING",
            index=True,
        ),
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 4. user_roles.organization_id -> FK (column + index already existed from 0002)
    op.create_foreign_key(
        "fk_user_roles_organization_id",
        "user_roles",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 5. audit_logs.organization_id -> FK (column + index already existed from 0002)
    op.create_foreign_key(
        "fk_audit_logs_organization_id",
        "audit_logs",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 6. security_events.organization_id -> new nullable column + FK + index
    op.add_column(
        "security_events",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_security_events_organization_id",
        "security_events",
        ["organization_id"],
    )
    op.create_foreign_key(
        "fk_security_events_organization_id",
        "security_events",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Note: the audit_event_type enum values added in upgrade() are NOT
    # removed here — PostgreSQL has no ALTER TYPE ... DROP VALUE. Any
    # audit_logs rows already written with a Phase 3 event_type would
    # otherwise be unreadable. This is a documented, accepted limitation of
    # additive enum migrations.
    op.drop_constraint(
        "fk_security_events_organization_id", "security_events", type_="foreignkey"
    )
    op.drop_index("ix_security_events_organization_id", table_name="security_events")
    op.drop_column("security_events", "organization_id")

    op.drop_constraint("fk_audit_logs_organization_id", "audit_logs", type_="foreignkey")
    op.drop_constraint("fk_user_roles_organization_id", "user_roles", type_="foreignkey")

    op.drop_table("organization_invitations")

    op.drop_index("ix_org_memberships_org_status", table_name="organization_memberships")
    op.drop_table("organization_memberships")

    op.drop_table("organizations")

    bind = op.get_bind()
    postgresql.ENUM(name="invitation_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="membership_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="organization_status").drop(bind, checkfirst=True)
