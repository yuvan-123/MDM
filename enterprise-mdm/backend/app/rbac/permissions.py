"""Permission string constants and built-in role definitions.

Permission strings follow the pattern `resource.action`.

These constants are used throughout the codebase to reference permissions
by name without magic strings. The actual Role/Permission records are
created by the database seeding process (scripts/seed_admin.py +
migrations) using these same strings, so a typo here would cause a
startup-time permission check failure rather than a silent security hole.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Perm:
    """Single permission definition for seeding."""

    name: str
    display_name: str
    resource: str
    action: str


# ---------------------------------------------------------------------------
# Permission string constants — use these throughout application code.
# ---------------------------------------------------------------------------

# Organization
ORG_READ = "organization.read"
ORG_CREATE = "organization.create"
ORG_UPDATE = "organization.update"
ORG_DELETE = "organization.delete"

# Organization members (Phase 3)
ORG_MEMBERS_READ = "organization.members.read"
ORG_MEMBERS_CREATE = "organization.members.create"
ORG_MEMBERS_UPDATE = "organization.members.update"
ORG_MEMBERS_REMOVE = "organization.members.remove"

# User
USER_READ = "user.read"
USER_CREATE = "user.create"
USER_UPDATE = "user.update"
USER_DELETE = "user.delete"

# Device
DEVICE_READ = "device.read"
DEVICE_CREATE = "device.create"
DEVICE_UPDATE = "device.update"
DEVICE_DELETE = "device.delete"
DEVICE_ENROLL = "device.enroll"
DEVICE_LOCK = "device.lock"
DEVICE_WIPE = "device.wipe"

# Device — inventory & lifecycle (Phase 4)
DEVICE_ASSIGN = "device.assign"
DEVICE_UNASSIGN = "device.unassign"
DEVICE_BLOCK = "device.block"
DEVICE_UNBLOCK = "device.unblock"
DEVICE_RETIRE = "device.retire"
DEVICE_VIEW_SENSITIVE = "device.view_sensitive"

# Policy
POLICY_READ = "policy.read"
POLICY_CREATE = "policy.create"
POLICY_UPDATE = "policy.update"
POLICY_DELETE = "policy.delete"
POLICY_ASSIGN = "policy.assign"
POLICY_UNASSIGN = "policy.unassign"

# Device groups (Phase 5)
DEVICE_GROUP_READ = "device_group.read"
DEVICE_GROUP_CREATE = "device_group.create"
DEVICE_GROUP_UPDATE = "device_group.update"
DEVICE_GROUP_DELETE = "device_group.delete"
DEVICE_GROUP_MANAGE_MEMBERS = "device_group.manage_members"

# Enrollment (Phase 5)
ENROLLMENT_TOKEN_CREATE = "enrollment_token.create"
ENROLLMENT_TOKEN_READ = "enrollment_token.read"
ENROLLMENT_TOKEN_REVOKE = "enrollment_token.revoke"

# Device commands (Phase 6) — LOCK and WIPE deliberately reuse the
# pre-existing DEVICE_LOCK/DEVICE_WIPE constants above rather than
# duplicating them under this namespace; read/history reuses DEVICE_READ,
# matching how enrollment's GET reuses device.read rather than inventing a
# new read permission.
DEVICE_COMMAND_REBOOT = "device.command.reboot"
DEVICE_COMMAND_REFRESH = "device.command.refresh"
DEVICE_COMMAND_POLICY_SYNC = "device.command.policy_sync"
DEVICE_COMMAND_CANCEL = "device.command.cancel"

# Device unenrollment via remote command (Phase 12) — deliberately its own
# permission, not folded into DEVICE_WIPE despite the same high-risk
# safeguard shape (confirm + required idempotency_key): wiping and
# unenrolling are different real-world decisions an admin can want
# independently of each other.
DEVICE_COMMAND_UNENROLL = "device.command.unenroll"

# Application
APP_READ = "application.read"
APP_CREATE = "application.create"
APP_UPDATE = "application.update"
APP_DELETE = "application.delete"
APP_ASSIGN = "application.assign"
# Application deployment (Phase 14) — deliberately separate from APP_ASSIGN:
# assigning an app to a scope and actually triggering a dispatch to real
# devices are different-weight actions, the same split POLICY_ASSIGN vs.
# COMPLIANCE_EVALUATE already draws between "configure" and "act now."
APP_DEPLOY = "application.deploy"

# Compliance
COMPLIANCE_READ = "compliance.read"
COMPLIANCE_EVALUATE = "compliance.evaluate"
# Policy violation remediation control (Phase 13) — retry/resolve/disable/
# enable are bundled under one permission (all "manage this violation's
# remediation"), distinct from COMPLIANCE_EVALUATE (triggering an
# ordinary evaluation) since these are explicit interventions on an
# already-detected problem, not routine read/evaluate actions.
COMPLIANCE_REMEDIATION_MANAGE = "compliance.remediation.manage"

# Audit
AUDIT_READ = "audit.read"

# Reports
REPORT_READ = "report.read"

# Background jobs & provider events (Phase 8) — operational visibility into
# the Phase 7 async infrastructure. No write/retry permission exists yet —
# Phase 8 exposes read-only observability only (see docs/architecture/phase-8.md).
JOB_READ = "job.read"
PROVIDER_EVENT_READ = "provider_event.read"

# Job/provider-event manual retry (Phase 10) — deliberately separate from
# JOB_READ/PROVIDER_EVENT_READ: an operator who can see a failed job/event
# should not automatically be able to re-trigger it (a retry is a real
# state-changing action, not a read).
JOB_RETRY = "job.retry"
PROVIDER_EVENT_RETRY = "provider_event.retry"

# Provider-device-link administration (Phase 10) — see
# app/provider_events/link_service.py.
PROVIDER_DEVICE_LINK_READ = "provider_device_link.read"
PROVIDER_DEVICE_LINK_CREATE = "provider_device_link.create"
PROVIDER_DEVICE_LINK_UPDATE = "provider_device_link.update"
PROVIDER_DEVICE_LINK_DELETE = "provider_device_link.delete"

# ---------------------------------------------------------------------------
# All permissions — used by the seeder to populate the permissions table.
# ---------------------------------------------------------------------------
ALL_PERMISSIONS: list[_Perm] = [
    _Perm(ORG_READ, "Read Organizations", "organization", "read"),
    _Perm(ORG_CREATE, "Create Organizations", "organization", "create"),
    _Perm(ORG_UPDATE, "Update Organizations", "organization", "update"),
    _Perm(ORG_DELETE, "Delete Organizations", "organization", "delete"),
    _Perm(ORG_MEMBERS_READ, "Read Organization Members", "organization.members", "read"),
    _Perm(ORG_MEMBERS_CREATE, "Invite Organization Members", "organization.members", "create"),
    _Perm(ORG_MEMBERS_UPDATE, "Update Organization Members", "organization.members", "update"),
    _Perm(ORG_MEMBERS_REMOVE, "Remove Organization Members", "organization.members", "remove"),
    _Perm(USER_READ, "Read Users", "user", "read"),
    _Perm(USER_CREATE, "Create Users", "user", "create"),
    _Perm(USER_UPDATE, "Update Users", "user", "update"),
    _Perm(USER_DELETE, "Delete Users", "user", "delete"),
    _Perm(DEVICE_READ, "Read Devices", "device", "read"),
    _Perm(DEVICE_CREATE, "Create Devices", "device", "create"),
    _Perm(DEVICE_UPDATE, "Update Devices", "device", "update"),
    _Perm(DEVICE_DELETE, "Delete Devices", "device", "delete"),
    _Perm(DEVICE_ENROLL, "Enroll Devices", "device", "enroll"),
    _Perm(DEVICE_LOCK, "Lock Devices", "device", "lock"),
    _Perm(DEVICE_WIPE, "Wipe Devices", "device", "wipe"),
    _Perm(DEVICE_ASSIGN, "Assign Devices", "device", "assign"),
    _Perm(DEVICE_UNASSIGN, "Unassign Devices", "device", "unassign"),
    _Perm(DEVICE_BLOCK, "Block Devices", "device", "block"),
    _Perm(DEVICE_UNBLOCK, "Unblock Devices", "device", "unblock"),
    _Perm(DEVICE_RETIRE, "Retire Devices", "device", "retire"),
    _Perm(DEVICE_VIEW_SENSITIVE, "View Sensitive Device Data", "device", "view_sensitive"),
    _Perm(POLICY_READ, "Read Policies", "policy", "read"),
    _Perm(POLICY_CREATE, "Create Policies", "policy", "create"),
    _Perm(POLICY_UPDATE, "Update Policies", "policy", "update"),
    _Perm(POLICY_DELETE, "Delete Policies", "policy", "delete"),
    _Perm(POLICY_ASSIGN, "Assign Policies", "policy", "assign"),
    _Perm(POLICY_UNASSIGN, "Unassign Policies", "policy", "unassign"),
    _Perm(DEVICE_GROUP_READ, "Read Device Groups", "device_group", "read"),
    _Perm(DEVICE_GROUP_CREATE, "Create Device Groups", "device_group", "create"),
    _Perm(DEVICE_GROUP_UPDATE, "Update Device Groups", "device_group", "update"),
    _Perm(DEVICE_GROUP_DELETE, "Delete Device Groups", "device_group", "delete"),
    _Perm(
        DEVICE_GROUP_MANAGE_MEMBERS,
        "Manage Device Group Members",
        "device_group",
        "manage_members",
    ),
    _Perm(ENROLLMENT_TOKEN_CREATE, "Create Enrollment Tokens", "enrollment_token", "create"),
    _Perm(ENROLLMENT_TOKEN_READ, "Read Enrollment Tokens", "enrollment_token", "read"),
    _Perm(ENROLLMENT_TOKEN_REVOKE, "Revoke Enrollment Tokens", "enrollment_token", "revoke"),
    _Perm(DEVICE_COMMAND_REBOOT, "Reboot Devices", "device.command", "reboot"),
    _Perm(DEVICE_COMMAND_REFRESH, "Refresh Device Info", "device.command", "refresh"),
    _Perm(DEVICE_COMMAND_POLICY_SYNC, "Sync Device Policy", "device.command", "policy_sync"),
    _Perm(DEVICE_COMMAND_CANCEL, "Cancel Device Commands", "device.command", "cancel"),
    _Perm(DEVICE_COMMAND_UNENROLL, "Unenroll Devices", "device.command", "unenroll"),
    _Perm(APP_READ, "Read Applications", "application", "read"),
    _Perm(APP_CREATE, "Create Applications", "application", "create"),
    _Perm(APP_UPDATE, "Update Applications", "application", "update"),
    _Perm(APP_DELETE, "Delete Applications", "application", "delete"),
    _Perm(APP_ASSIGN, "Assign Applications", "application", "assign"),
    _Perm(APP_DEPLOY, "Deploy Applications", "application", "deploy"),
    _Perm(COMPLIANCE_READ, "Read Compliance", "compliance", "read"),
    _Perm(COMPLIANCE_EVALUATE, "Trigger Compliance Evaluation", "compliance", "evaluate"),
    _Perm(
        COMPLIANCE_REMEDIATION_MANAGE,
        "Manage Violation Remediation",
        "compliance.remediation",
        "manage",
    ),
    _Perm(AUDIT_READ, "Read Audit Logs", "audit", "read"),
    _Perm(REPORT_READ, "Read Reports", "report", "read"),
    _Perm(JOB_READ, "Read Background Jobs", "job", "read"),
    _Perm(PROVIDER_EVENT_READ, "Read Provider Events", "provider_event", "read"),
    _Perm(JOB_RETRY, "Retry Background Jobs", "job", "retry"),
    _Perm(PROVIDER_EVENT_RETRY, "Retry Provider Events", "provider_event", "retry"),
    _Perm(PROVIDER_DEVICE_LINK_READ, "Read Provider Device Links", "provider_device_link", "read"),
    _Perm(
        PROVIDER_DEVICE_LINK_CREATE,
        "Create Provider Device Links",
        "provider_device_link",
        "create",
    ),
    _Perm(
        PROVIDER_DEVICE_LINK_UPDATE,
        "Update Provider Device Links",
        "provider_device_link",
        "update",
    ),
    _Perm(
        PROVIDER_DEVICE_LINK_DELETE,
        "Delete Provider Device Links",
        "provider_device_link",
        "delete",
    ),
]

# ---------------------------------------------------------------------------
# Built-in role definitions
# ---------------------------------------------------------------------------

# Role names — reference these constants instead of raw strings.
SUPER_ADMIN = "SUPER_ADMIN"
ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN"
SECURITY_ADMIN = "SECURITY_ADMIN"
HELP_DESK = "HELP_DESK"
AUDITOR = "AUDITOR"


@dataclass(frozen=True)
class _RoleDef:
    name: str
    display_name: str
    description: str
    permissions: frozenset[str]


BUILT_IN_ROLES: list[_RoleDef] = [
    _RoleDef(
        name=SUPER_ADMIN,
        display_name="Super Administrator",
        description="Platform-level administrator with all permissions. Organization-agnostic.",
        permissions=frozenset(p.name for p in ALL_PERMISSIONS),
    ),
    _RoleDef(
        name=ORGANIZATION_ADMIN,
        display_name="Organization Administrator",
        description="Full administrator within an organization.",
        permissions=frozenset(
            [
                ORG_READ,
                ORG_UPDATE,
                ORG_MEMBERS_READ,
                ORG_MEMBERS_CREATE,
                ORG_MEMBERS_UPDATE,
                ORG_MEMBERS_REMOVE,
                USER_READ,
                USER_CREATE,
                USER_UPDATE,
                USER_DELETE,
                DEVICE_READ,
                DEVICE_CREATE,
                DEVICE_UPDATE,
                DEVICE_DELETE,
                DEVICE_ENROLL,
                DEVICE_LOCK,
                DEVICE_WIPE,
                DEVICE_ASSIGN,
                DEVICE_UNASSIGN,
                DEVICE_BLOCK,
                DEVICE_UNBLOCK,
                DEVICE_RETIRE,
                DEVICE_VIEW_SENSITIVE,
                POLICY_READ,
                POLICY_CREATE,
                POLICY_UPDATE,
                POLICY_DELETE,
                POLICY_ASSIGN,
                POLICY_UNASSIGN,
                DEVICE_GROUP_READ,
                DEVICE_GROUP_CREATE,
                DEVICE_GROUP_UPDATE,
                DEVICE_GROUP_DELETE,
                DEVICE_GROUP_MANAGE_MEMBERS,
                ENROLLMENT_TOKEN_CREATE,
                ENROLLMENT_TOKEN_READ,
                ENROLLMENT_TOKEN_REVOKE,
                DEVICE_COMMAND_REBOOT,
                DEVICE_COMMAND_REFRESH,
                DEVICE_COMMAND_POLICY_SYNC,
                DEVICE_COMMAND_CANCEL,
                DEVICE_COMMAND_UNENROLL,
                APP_READ,
                APP_CREATE,
                APP_UPDATE,
                APP_DELETE,
                APP_ASSIGN,
                APP_DEPLOY,
                COMPLIANCE_READ,
                COMPLIANCE_EVALUATE,
                COMPLIANCE_REMEDIATION_MANAGE,
                AUDIT_READ,
                REPORT_READ,
                JOB_READ,
                JOB_RETRY,
                PROVIDER_EVENT_READ,
                PROVIDER_EVENT_RETRY,
                PROVIDER_DEVICE_LINK_READ,
                PROVIDER_DEVICE_LINK_CREATE,
                PROVIDER_DEVICE_LINK_UPDATE,
                PROVIDER_DEVICE_LINK_DELETE,
            ]
        ),
    ),
    _RoleDef(
        name=SECURITY_ADMIN,
        display_name="Security Administrator",
        description="Manages security policies, compliance, and audit logs.",
        permissions=frozenset(
            [
                USER_READ,
                ORG_MEMBERS_READ,
                DEVICE_READ,
                DEVICE_LOCK,
                DEVICE_WIPE,
                DEVICE_BLOCK,
                DEVICE_UNBLOCK,
                DEVICE_VIEW_SENSITIVE,
                POLICY_READ,
                POLICY_CREATE,
                POLICY_UPDATE,
                POLICY_ASSIGN,
                POLICY_UNASSIGN,
                DEVICE_GROUP_READ,
                ENROLLMENT_TOKEN_READ,
                DEVICE_COMMAND_REBOOT,
                DEVICE_COMMAND_REFRESH,
                DEVICE_COMMAND_POLICY_SYNC,
                DEVICE_COMMAND_CANCEL,
                DEVICE_COMMAND_UNENROLL,
                APP_READ,
                APP_DEPLOY,
                COMPLIANCE_READ,
                COMPLIANCE_EVALUATE,
                COMPLIANCE_REMEDIATION_MANAGE,
                AUDIT_READ,
                REPORT_READ,
                JOB_READ,
                JOB_RETRY,
                PROVIDER_EVENT_READ,
                PROVIDER_EVENT_RETRY,
                PROVIDER_DEVICE_LINK_READ,
                PROVIDER_DEVICE_LINK_CREATE,
                PROVIDER_DEVICE_LINK_UPDATE,
                PROVIDER_DEVICE_LINK_DELETE,
            ]
        ),
    ),
    _RoleDef(
        name=HELP_DESK,
        display_name="Help Desk",
        description="Can view and lock devices; cannot wipe or manage policies.",
        permissions=frozenset(
            [
                USER_READ,
                ORG_MEMBERS_READ,
                DEVICE_READ,
                DEVICE_LOCK,
                DEVICE_ASSIGN,
                DEVICE_UNASSIGN,
                DEVICE_ENROLL,
                DEVICE_GROUP_READ,
                ENROLLMENT_TOKEN_CREATE,
                ENROLLMENT_TOKEN_READ,
                DEVICE_COMMAND_REFRESH,
                POLICY_READ,
                APP_READ,
                COMPLIANCE_READ,
            ]
        ),
    ),
    _RoleDef(
        name=AUDITOR,
        display_name="Auditor",
        description="Read-only access to audit logs, reports, and device/policy state.",
        permissions=frozenset(
            [
                ORG_READ,
                ORG_MEMBERS_READ,
                USER_READ,
                DEVICE_READ,
                POLICY_READ,
                DEVICE_GROUP_READ,
                ENROLLMENT_TOKEN_READ,
                APP_READ,
                COMPLIANCE_READ,
                AUDIT_READ,
                REPORT_READ,
                JOB_READ,
                PROVIDER_EVENT_READ,
                PROVIDER_DEVICE_LINK_READ,
            ]
        ),
    ),
]
