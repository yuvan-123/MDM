# Multi-Tenancy Architecture

Status: Phase 3 implemented (Organizations, Memberships, Invitations, org-scoped RBAC).
This document reflects the architecture actually implemented, superseding the
Phase 1 placeholder version.

## 1. Core Rule

**Tenant A must never be able to read, modify, or infer the existence of
Tenant B's data.** This is the single most security-critical property of the
platform and is treated as such throughout the design.

Tenancy unit = `organization`. Every tenant-owned table carries an
`organization_id` foreign key. There are no cross-tenant shared resources
except platform-level configuration (roles/permissions definitions, which
are data-driven templates, not tenant data).

## 2. Isolation Enforced at Four Layers

This is intentionally redundant — any single layer failing must not result
in a cross-tenant leak.

1. **API layer**: every organization-scoped request resolves an
   `OrganizationContext` (`app/organizations/dependencies.py`) before any
   handler runs. The context is always re-derived from the database on
   every request — never cached in a JWT claim or server-side session —
   the same principle `get_current_user` already applies to permissions
   (see `docs/security/authentication.md`: "authorization is always
   re-checked server-side... to prevent stale permission escalation").
2. **Service layer**: `OrganizationService` / `MembershipService` accept
   the tenant context as an explicit parameter and never receive a "trust
   me" flag to skip it. There is no service method that takes a bare
   resource ID without also taking the caller's organization context.
3. **Repository/database layer**: every tenant-scoped repository method
   requires `organization_id` explicitly — e.g.
   `MembershipRepository.get_for_organization(organization_id, membership_id)`
   — never a bare `get_by_id(membership_id)`. A cross-tenant ID simply
   doesn't match the `WHERE` clause and returns `None`, which the caller
   turns into 404, not a data leak.
4. **Testing layer**: `tests/test_tenant_isolation.py` is the mandatory
   cross-tenant proof — two organizations, cross-tenant GET/PATCH/invite/
   role-modification all denied, plus the 403-vs-404 distinction (§6 below)
   explicitly asserted both ways.

## 3. Tenant Context Resolution

No `organization_id` JWT claim exists, and no server-side "active
organization" session state exists — both would risk staleness if
membership or role assignments change mid-session, the same risk that
kept permissions out of the JWT in Phase 2.

Two resolution paths, sharing one validator
(`app/organizations/dependencies.py::resolve_organization_context`):

- **Path-based** (`get_organization_context`): every
  `/organizations/{organization_id}/...` route reads `organization_id`
  directly from the URL — there is no ambiguity to resolve.
- **Header-based** (`get_current_organization`): for endpoints with no org
  id in their path (used today by `/auth/select-organization`'s
  validation step; reserved for Phase 4+ non-nested tenant-scoped
  endpoints, e.g. a future `/devices`). Reads `X-Organization-ID`. If
  absent and the caller has exactly one ACTIVE membership, that
  organization is used automatically. If absent and ambiguous, the
  request is rejected with `400 ORGANIZATION_CONTEXT_REQUIRED`.

In both paths, the resolved value is **only ever a routing hint**. Every
request independently re-validates: the organization exists and is
`ACTIVE`, the caller has an `ACTIVE` `OrganizationMembership` in it (or is
a platform `SUPER_ADMIN`, which bypasses the membership requirement), and
resolves the caller's effective org-scoped permission set fresh from
`user_roles` filtered by that `organization_id`. A malicious or stale
`X-Organization-ID` value never grants anything — it is validated as
untrusted client input every single time (see `app/organizations/schemas.py`'s
`SelectOrganizationRequest.organization_id` docstring for the same
principle applied to the request-body equivalent).

## 4. Multi-Organization Membership

**A single user can hold roles in multiple organizations, each
independently scoped** (e.g. `ORGANIZATION_ADMIN` in Org A, `AUDITOR` in
Org B) — this supersedes an earlier Phase 1 placeholder note that assumed
single-org-per-user; that assumption was never implemented and conflicted
with the RBAC design (`docs/security/rbac.md` §4), which always specified
multi-org role assignment via `UserRole.organization_id`.

Mechanically: `UserRole.organization_id` (nullable, now FK-constrained to
`organizations.id`) is reused directly rather than introducing a second,
parallel role-assignment table — `NULL` = platform-scoped role (only
`SUPER_ADMIN` in practice), non-`NULL` = that role scoped to that
organization. `OrganizationMembership` is the separate table that tracks
membership *status* (`ACTIVE`/`INVITED`/`SUSPENDED`/`REMOVED`) independent
of which roles a member holds — a user can be an ACTIVE member with zero
extra roles, or have their membership suspended without losing the role
grant history.

`RBACRepository.get_permissions_for_user` (used by the platform
`CurrentUser`/`require_permission` on routes like `/api/v1/users`) filters
`UserRole.organization_id IS NULL` — **org-scoped role grants deliberately
do not leak into the platform permission set.** Without this filter, a
user with `ORGANIZATION_ADMIN` (which includes `user.create`) scoped to
one organization would pass platform-level `require_permission("user.create")`
checks and manage the *global* user directory — a cross-tenant privilege
escalation this filter exists specifically to prevent. Org-scoped
permissions are resolved separately, only inside a validated
`OrganizationContext`, via `RBACRepository.get_org_scoped_permissions`.

## 5. Row Level Security — Deferred, Not Implemented

PostgreSQL Row Level Security was evaluated for this phase and
**deliberately deferred**, not implemented, for a concrete reason: the
test suite (`tests/conftest.py`) runs against SQLite in-memory for speed,
which has no RLS/GUC support. Testing RLS policies would require standing
up a parallel PostgreSQL test harness — disproportionate scope for this
phase, and it would not replace application-layer filtering (§2) as the
enforced boundary regardless.

The schema is kept RLS-compatible for when it's added: `organization_id`
is present and indexed on every tenant-scoped table
(`organization_memberships`, `user_roles`, `audit_logs`, `security_events`,
and every domain table from Phase 4 onward). The future design: a
per-request `SET LOCAL app.current_organization_id` GUC set at the start
of each request's DB transaction (most naturally in a middleware or a
session-scoped dependency wrapping `get_db_session`), with `CREATE POLICY`
statements on each tenant table filtering `organization_id = current_setting('app.current_organization_id')::uuid`.
This is a backstop layer for when it's added — application-layer
filtering remains mandatory either way.

## 6. Cross-Tenant Response Codes: 403 vs. 404

Two different, intentional responses depending on what's being denied
(both are explicitly tested in `tests/test_tenant_isolation.py`):

- **`403 ORGANIZATION_ACCESS_DENIED`** — a non-member (and non-super-admin)
  directly requesting an organization by ID
  (`GET/PATCH /organizations/{id}`, `GET/POST /organizations/{id}/members`).
  The organization's existence isn't itself sensitive information (a
  `SUPER_ADMIN` can list all organizations; slugs may be visible through
  other channels), so a direct denial is used.
- **`404 NOT_FOUND`** — a nested resource whose ID belongs to a *different*
  organization than the one already authorized by the request path (e.g.
  PATCHing `/organizations/{org_a_id}/members/{membership_that_belongs_to_org_b}`).
  This falls out naturally from the repository pattern in §2.3 —
  `get_for_organization(org_id, resource_id)` simply finds no matching row
  — and is preferable here because the caller is already inside a
  validated org context; confirming that a *specific ID* exists in another
  tenant is exactly the kind of existence-inference the core rule (§1)
  prohibits.

## 7. Anti-Patterns Explicitly Disallowed

- Filtering tenant data only in the frontend (e.g. hiding rows in a table
  the API already returned) — this leaks data over the wire regardless of
  UI behavior.
- A repository method that takes only a primary key with no tenant filter,
  even if every current caller happens to pass the right ID — the method
  signature itself is the vulnerability.
- Using a global "admin bypass" flag that skips tenant filtering without an
  explicit, audited, super-admin-only code path (`OrganizationContext.is_platform_super_admin`
  is the one sanctioned exception, and every use of it is auditable —
  `ROLE_ASSIGNED`/`MEMBER_REMOVED`/etc. events always record `actor_user_id`
  regardless of whether the actor took the super-admin path).
- Trusting an `organization_id` value from client-supplied JSON body or
  headers for authorization decisions without re-validating membership —
  see §3.

## 8. Frontend Cache Isolation

TanStack Query keys for every organization-scoped resource include the
organization id (`lib/organizations.ts`'s `ORG_KEYS`): e.g.
`["organizations", organizationId, "members"]`, never `["members"]`.
Switching the active organization (`useSelectOrganization`) invalidates
every query whose key starts with `"organizations"`, so no stale data
from a previously selected organization can render after a switch. The
active-organization id itself lives in a small module-level store
(`lib/organization-store.ts`), persisted to `localStorage` and read
synchronously by `lib/api/client.ts` to attach `X-Organization-ID` — not
in React Context, since the fetch wrapper needs to read it before any
component tree exists.
