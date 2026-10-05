# Role-Based Access Control (RBAC) Architecture (Phase 2 + Phase 3)

## 1. Principles

- **Permission-based, not role-name-based**: Authorization logic checks fine-grained permissions (`require_permission("device.wipe")`), never hardcoded role string checks like `if user.role == "ADMIN"`.
- **Data-driven**: Roles and permissions are stored in database tables (`roles`, `permissions`, `role_permissions`, `user_roles`), allowing operators to define custom roles without deploying code.
- **Server-side enforcement**: Frontend route guards are UX only. Backend authorization dependencies independently verify permissions on every API invocation.
- **Platform vs. organization scope**: `user_roles.organization_id` (nullable, FK-constrained to `organizations.id` as of Phase 3) is the single mechanism for both. `NULL` = platform-level role (only `SUPER_ADMIN` in practice); non-`NULL` = that role scoped to that organization. See §4.

---

## 2. Permission Model

Permissions follow a `resource.action` naming convention:

| Resource | Actions | Example Permissions |
|---|---|---|
| `organization` | read, create, update, delete | `organization.read`, `organization.update` |
| `user` | read, create, update, delete | `user.read`, `user.create`, `user.update` |
| `device` | read, create, update, delete, enroll, lock, wipe | `device.read`, `device.lock`, `device.wipe` |
| `policy` | read, create, update, delete, assign | `policy.read`, `policy.assign` |
| `application` | read, create, update, delete, assign | `application.read`, `application.assign` |
| `organization.members` | read, create, update, remove | `organization.members.read`, `organization.members.create` |
| `compliance` | read | `compliance.read` |
| `audit` | read | `audit.read` |
| `report` | read | `report.read` |

---

## 3. Built-In Roles

| Role Name | Display Name | Scope | Description / Permissions |
|---|---|---|---|
| `SUPER_ADMIN` | Super Administrator | Platform | All platform & tenant permissions. |
| `ORGANIZATION_ADMIN` | Organization Administrator | Organization | Full tenant administration (org, users, devices, policies, apps, audit). |
| `SECURITY_ADMIN` | Security Administrator | Organization | Manage security policies, compliance, locking, wiping, and audit logs. |
| `HELP_DESK` | Help Desk | Organization | View users/devices, issue device lock commands. No wipe or policy editing. |
| `AUDITOR` | Auditor | Organization | Read-only access to audit logs, reports, devices, and policies. |

---

## 4. Multi-Tenancy Scoping (Phase 3 — implemented)

`UserRole.organization_id` is the single mechanism for both platform and
organization-scoped roles — Phase 3 did **not** introduce a second,
parallel role-assignment table (see `docs/architecture/multi-tenancy.md`
§4 for the rationale: the column, and its
`UniqueConstraint(user_id, role_id, organization_id)`, already supported
this exact shape since Phase 2).

1. `UserRole` entries for `SUPER_ADMIN` remain platform-scoped
   (`organization_id = NULL`) — enforced by convention (`invite_member`
   and `assign_org_role` both reject assigning `SUPER_ADMIN` with a
   non-`NULL` organization_id) rather than a DB constraint, since a DB
   `CHECK` can't easily express "only this one role name."
2. Tenant role assignments (`ORGANIZATION_ADMIN`, `SECURITY_ADMIN`,
   `HELP_DESK`, `AUDITOR`) populate `organization_id` with the target
   organization's id.
3. **Two independent permission-resolution paths, deliberately kept
   separate**:
   - `RBACRepository.get_permissions_for_user` (used by the platform
     `CurrentUser`/`require_permission`, gating routes like
     `/api/v1/users`) filters `organization_id IS NULL` — org-scoped
     grants never leak into platform permissions. This filter was added
     specifically in Phase 3; before organizations existed every
     `UserRole` row had `organization_id = NULL` regardless, so the
     filter is a no-op for pre-Phase-3 data but became load-bearing the
     moment org-scoped roles started being granted.
   - `RBACRepository.get_org_scoped_permissions(user_id, organization_id)`
     (used by `OrganizationContext`, only inside a validated
     `resolve_organization_context` call) filters
     `organization_id == :organization_id`.
4. A single user can hold different roles across multiple organizations
   (e.g. `ORGANIZATION_ADMIN` in Org A, `AUDITOR` in Org B) —
   `tests/test_multi_tenancy.py::test_user_can_hold_different_roles_in_two_organizations`
   verifies the effective permission set actually differs per organization.
5. Platform `SUPER_ADMIN` bypasses the membership requirement entirely
   when addressing any organization (`OrganizationContext.is_platform_super_admin`),
   but every action taken is still audited under the real actor's user id
   — see `docs/security/multi-tenancy-security-review.md`.
