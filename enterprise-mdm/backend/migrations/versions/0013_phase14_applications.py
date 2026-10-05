"""Phase 14: enterprise application management.

Revision ID: 0013_phase14_applications
Revises: 0012_phase13_policy_violations
Create Date: 2026-08-25

Creates:
- applications — the app catalog. See app/applications/models.py::Application
  for the platform-identifier-isolation design (package_name XOR
  bundle_identifier, enforced by a CHECK constraint here and validated
  again at the service layer).
- application_assignments — same polymorphic scope_type/scope_id shape as
  policy_assignments (Phase 5), reusing the existing `policy_scope_type`
  enum rather than duplicating it.
- application_deployments — one row per (organization, device,
  application); the current-state anchor whose full attempt history lives
  in device_commands (see app/applications/models.py::ApplicationDeployment).

Alters:
- device_command_type enum: adds INSTALL_APPLICATION/UNINSTALL_APPLICATION
  (additive, matches the 0006/.../0011 precedent).
- audit_event_type enum: adds 7 Phase 14 event types (additive only).

No changes to policies, policy_versions, policy_assignments,
policy_violations, device_commands, or background_jobs schemas — app
deployment reuses DeviceCommand/CommandService and BackgroundJob/JobService
entirely unchanged; see app/applications/service.py's module docstring for
why PolicyViolation/RemediationPolicyConfig are deliberately NOT extended
for required-app tracking instead of a new table.
"""

from __future__ import annotations

import alembic.op as op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0013_phase14_applications"
down_revision = "0012_phase13_policy_violations"
branch_labels = None
depends_on = None

_NEW_DEVICE_COMMAND_TYPES = ["INSTALL_APPLICATION", "UNINSTALL_APPLICATION"]

_NEW_AUDIT_EVENT_TYPES = [
    "APPLICATION_CREATED",
    "APPLICATION_UPDATED",
    "APPLICATION_DELETED",
    "APPLICATION_ASSIGNED",
    "APPLICATION_UNASSIGNED",
    "APPLICATION_DEPLOYMENT_TRIGGERED",
    "APPLICATION_REMOVAL_TRIGGERED",
]


def upgrade() -> None:
    # -- Additive enum extensions -------------------------------------------
    for command_type in _NEW_DEVICE_COMMAND_TYPES:
        op.execute(f"ALTER TYPE device_command_type ADD VALUE IF NOT EXISTS '{command_type}'")
    for event_type in _NEW_AUDIT_EVENT_TYPES:
        op.execute(f"ALTER TYPE audit_event_type ADD VALUE IF NOT EXISTS '{event_type}'")

    # -- References to enums created in earlier phases (create_type=False,
    #    matches 0005's device_platform_ref precedent) ----------------------
    device_platform_ref = postgresql.ENUM(
        "ANDROID", "IOS", "IPADOS", "OTHER", name="device_platform", create_type=False
    )
    policy_scope_type_ref = postgresql.ENUM(
        "ORGANIZATION", "USER", "DEVICE_GROUP", "DEVICE", name="policy_scope_type",
        create_type=False,
    )

    # -- New enums ------------------------------------------------------
    application_requirement = postgresql.ENUM(
        "REQUIRED", "OPTIONAL", name="application_requirement"
    )
    application_requirement.create(op.get_bind(), checkfirst=True)
    application_requirement.create_type = False

    application_deployment_status = postgresql.ENUM(
        "PENDING", "INSTALLING", "INSTALLED", "FAILED", "REMOVED",
        name="application_deployment_status",
    )
    application_deployment_status.create(op.get_bind(), checkfirst=True)
    application_deployment_status.create_type = False

    # -- applications -----------------------------------------------------
    op.create_table(
        "applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("platform", device_platform_ref, nullable=False, index=True),
        sa.Column("package_name", sa.String(255), nullable=True),
        sa.Column("bundle_identifier", sa.String(255), nullable=True),
        sa.Column("version", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("managed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
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
        sa.UniqueConstraint(
            "organization_id",
            "platform",
            "package_name",
            name="uq_application_org_platform_package",
        ),
        sa.UniqueConstraint(
            "organization_id", "platform", "bundle_identifier",
            name="uq_application_org_platform_bundle",
        ),
        sa.CheckConstraint(
            "(platform = 'ANDROID' AND package_name IS NOT NULL AND bundle_identifier IS NULL) "
            "OR (platform IN ('IOS', 'IPADOS') AND bundle_identifier IS NOT NULL "
            "AND package_name IS NULL) "
            "OR (platform = 'OTHER')",
            name="ck_application_platform_identifier",
        ),
    )
    op.create_index("ix_applications_org_platform", "applications", ["organization_id", "platform"])

    # -- application_assignments --------------------------------------------
    op.create_table(
        "application_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("scope_type", policy_scope_type_ref, nullable=False, index=True),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column(
            "requirement", application_requirement, nullable=False, server_default="OPTIONAL"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "application_id", "scope_type", "scope_id", name="uq_application_assignment_scope"
        ),
    )

    # -- application_deployments ----------------------------------------
    op.create_table(
        "application_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "application_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("application_assignments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requirement", application_requirement, nullable=False),
        sa.Column(
            "status",
            application_deployment_status,
            nullable=False,
            server_default="PENDING",
            index=True,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_command_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("device_commands.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("installed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
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
            "organization_id",
            "device_id",
            "application_id",
            name="uq_application_deployment_device_app",
        ),
    )
    op.create_index(
        "ix_application_deployments_org_device",
        "application_deployments",
        ["organization_id", "device_id"],
    )
    op.create_index(
        "ix_application_deployments_org_app",
        "application_deployments",
        ["organization_id", "application_id"],
    )


def downgrade() -> None:
    # Note: device_command_type and audit_event_type values added in
    # upgrade() are NOT removed — PostgreSQL has no ALTER TYPE ... DROP
    # VALUE, matching every prior phase's downgrade precedent.
    op.drop_index("ix_application_deployments_org_app", table_name="application_deployments")
    op.drop_index("ix_application_deployments_org_device", table_name="application_deployments")
    op.drop_table("application_deployments")
    op.drop_table("application_assignments")
    op.drop_index("ix_applications_org_platform", table_name="applications")
    op.drop_table("applications")

    bind = op.get_bind()
    postgresql.ENUM(name="application_deployment_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="application_requirement").drop(bind, checkfirst=True)
