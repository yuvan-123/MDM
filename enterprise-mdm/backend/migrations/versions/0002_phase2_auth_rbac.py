"""Phase 2 Authentication and RBAC tables.

Revision ID: 0002_phase2_auth_rbac
Revises: 
Create Date: 2026-08-19

Creates:
- users
- roles
- permissions
- role_permissions
- user_roles
- sessions
- password_reset_tokens
- email_verification_tokens
- audit_logs
- security_events
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_phase2_auth_rbac"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres ENUM types
    user_status = postgresql.ENUM(
        "ACTIVE", "INVITED", "SUSPENDED", "LOCKED", "DISABLED", name="user_status"
    )
    user_status.create(op.get_bind(), checkfirst=True)
    user_status.create_type = False

    audit_event_type = postgresql.ENUM(
        "LOGIN_SUCCESS",
        "LOGIN_FAILED",
        "LOGOUT",
        "LOGOUT_ALL",
        "TOKEN_REFRESH",
        "TOKEN_REUSE_DETECTED",
        "PASSWORD_CHANGED",
        "PASSWORD_RESET_REQUESTED",
        "PASSWORD_RESET_COMPLETED",
        "EMAIL_VERIFIED",
        "EMAIL_VERIFICATION_SENT",
        "SESSION_REVOKED",
        "ACCOUNT_LOCKED",
        "ACCOUNT_UNLOCKED",
        "ROLE_ASSIGNED",
        "ROLE_REMOVED",
        "PERMISSION_DENIED",
        "USER_CREATED",
        "USER_UPDATED",
        "USER_STATUS_CHANGED",
        name="audit_event_type",
    )
    audit_event_type.create(op.get_bind(), checkfirst=True)
    audit_event_type.create_type = False

    sec_severity = postgresql.ENUM(
        "LOW", "MEDIUM", "HIGH", "CRITICAL", name="security_event_severity"
    )
    sec_severity.create(op.get_bind(), checkfirst=True)
    sec_severity.create_type = False

    sec_type = postgresql.ENUM(
        "BRUTE_FORCE_DETECTED",
        "REFRESH_TOKEN_REUSE",
        "ACCOUNT_LOCKED_BRUTE_FORCE",
        "REPEATED_PERMISSION_FAILURES",
        "SUSPICIOUS_IP_ACTIVITY",
        "INVALID_TOKEN_PRESENTED",
        name="security_event_type",
    )
    sec_type.create(op.get_bind(), checkfirst=True)
    sec_type.create_type = False

    # 1. users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True, index=True),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "status",
            user_status,
            nullable=False,
            server_default="ACTIVE",
            index=True,
        ),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("mfa_totp_secret", sa.Text(), nullable=True),
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

    # 2. roles
    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system_role", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 3. permissions
    op.create_table(
        "permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("resource", sa.String(100), nullable=False, index=True),
        sa.Column("action", sa.String(100), nullable=False),
    )

    # 4. role_permissions
    op.create_table(
        "role_permissions",
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # 5. user_roles
    op.create_table(
        "user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
        sa.UniqueConstraint("user_id", "role_id", "organization_id", name="uq_user_role_org"),
    )

    # 6. sessions
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "refresh_token_hash",
            sa.String(64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True, index=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("device_identifier", sa.String(255), nullable=True),
    )

    # 7. password_reset_tokens
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 8. email_verification_tokens
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 9. audit_logs
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", audit_event_type, nullable=False, index=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column(
            "target_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True, index=True),
        sa.Column("event_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )

    # 10. security_events
    op.create_table(
        "security_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sec_type, nullable=False, index=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("severity", sec_severity, nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("security_events")
    op.drop_table("audit_logs")
    op.drop_table("email_verification_tokens")
    op.drop_table("password_reset_tokens")
    op.drop_table("sessions")
    op.drop_table("user_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")

    bind = op.get_bind()
    postgresql.ENUM(name="security_event_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="security_event_severity").drop(bind, checkfirst=True)
    postgresql.ENUM(name="audit_event_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="user_status").drop(bind, checkfirst=True)
