# Phase 3 — Multi-Tenancy Security Review

## 1. Scope

Organization/tenant isolation, organization membership, invitations, and
org-scoped RBAC introduced in Phase 3. This review focuses specifically on
tenant-boundary correctness — see `docs/security/phase-2-security-review.md`
for the authentication/RBAC review this builds on.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **IDOR — direct organization access** | Every `/organizations/{id}/...` route resolves `OrganizationContext` server-side; a non-member (non-super-admin) is denied before any handler logic runs. | `tests/test_tenant_isolation.py` | PASS |
| **IDOR — nested resource cross-tenant reference** | Every tenant-scoped repository method requires `organization_id` explicitly (`MembershipRepository.get_for_organization(org_id, membership_id)`); a cross-tenant id resolves to `None` → 404, never the wrong tenant's row. | `tests/test_tenant_isolation.py::test_cross_tenant_nested_membership_returns_404_not_403` | PASS |
| **Horizontal privilege escalation — role self-modification** | `MembershipService.assign_org_role`/`remove_org_role` reject `membership.user_id == actor_user_id` unless the actor is platform `SUPER_ADMIN`. | `tests/test_organization_rbac.py::test_self_role_modification_denied` | PASS |
| **Vertical privilege escalation — granting SUPER_ADMIN via org role assignment** | `invite_member` and `assign_org_role` both explicitly reject `role_name == SUPER_ADMIN`; the platform role can never carry a non-`NULL` `organization_id`. | `tests/test_organization_rbac.py::test_org_admin_cannot_grant_super_admin`, `tests/test_memberships.py::test_invite_rejects_super_admin_role` | PASS |
| **Cross-tenant permission leakage into platform routes** | `RBACRepository.get_permissions_for_user` (backs the platform `CurrentUser`/`require_permission`) filters `organization_id IS NULL` — an org-scoped `ORGANIZATION_ADMIN` grant (which includes `user.create`) cannot be used to pass `require_permission("user.create")` on the platform `/api/v1/users` endpoint. | `tests/test_organization_rbac.py::test_platform_permissions_unaffected_by_org_scoped_roles` | PASS |
| **Untrusted client-supplied tenant context** | `X-Organization-ID` header and `SelectOrganizationRequest.organization_id` are both routing hints only — every request re-validates real membership + org status from the database, never trusts the value for authorization. | `tests/test_multi_tenancy.py::test_select_organization_rejects_non_member_org` | PASS |
| **Suspended organization access** | `resolve_organization_context` checks `org.status == ACTIVE` before membership resolution (for non-super-admins); non-`ACTIVE` orgs return `403 ORGANIZATION_SUSPENDED`. | `tests/test_organizations.py::test_member_of_suspended_organization_denied_access` | PASS |
| **Suspended membership access** | `resolve_organization_context` checks `membership.status == ACTIVE`; a `SUSPENDED` membership returns `403 MEMBERSHIP_SUSPENDED` even though the organization itself is active. | `tests/test_memberships.py::test_suspend_member` | PASS |
| **Membership removal doesn't fully revoke access** | `MembershipService.remove_member` explicitly revokes every org-scoped `UserRole` the user holds in that organization (not just flipping the membership status flag), so a removed member has zero effective permissions in that org on their very next request. | `tests/test_memberships.py::test_remove_member_revokes_org_access` | PASS |
| **Invitation token reuse / expiry** | Single-use (`InvitationRepository.get_valid_by_token_hash` filters `status == PENDING`; `mark_accepted` flips it), time-boxed (`expires_at`), hashed at rest (SHA-256, same pattern as password-reset tokens — raw token never persisted). | `tests/test_memberships.py::test_invitation_reuse_prevented`, `test_invitation_expiry` | PASS |
| **Organization self-suspension** | `PATCH /organizations/{id}/status` is gated by the *platform* `organization.update` permission (`require_permission`, not `require_organization_permission`) — an `ORGANIZATION_ADMIN` cannot suspend/disable their own organization. | `tests/test_organizations.py::test_org_admin_cannot_suspend_own_organization` | PASS |
| **Audit trail for tenant actions** | `ORGANIZATION_CREATED/UPDATED/SUSPENDED/DISABLED`, `MEMBER_INVITED/ACCEPTED/UPDATED/REMOVED/SUSPENDED` all logged with `actor_user_id` + `organization_id`, including when the actor took the `SUPER_ADMIN` bypass path. | `app/organizations/service.py`, `app/organizations/membership_service.py` | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **Cross-tenant permission leak via unfiltered platform permission
   resolution.** `RBACRepository.get_permissions_for_user` had no
   `organization_id` filter — harmless under Phase 2 (every `UserRole` row
   had `organization_id = NULL` anyway) but would have let an org-scoped
   `ORGANIZATION_ADMIN` grant (includes `user.create`, `user.delete`, etc.)
   leak into the platform permission set and pass `require_permission`
   checks on `/api/v1/users`, a platform-global endpoint. **Fixed** by
   filtering `UserRole.organization_id.is_(None)` in that method, with a
   dedicated `get_org_scoped_permissions` method added for the
   organization-context path. Caught proactively while designing the
   reuse of `UserRole.organization_id`, before any test was written against
   it — see `tests/test_organization_rbac.py::test_platform_permissions_unaffected_by_org_scoped_roles`
   for the regression guard.
2. **`MissingGreenlet` on `OrganizationResponse` serialization after status/name
   updates.** `TimestampMixin.updated_at` (`onupdate=func.now()`, server-computed)
   is marked "expired" by SQLAlchemy after any UPDATE — both a bare bulk
   `update()` SQL statement and a `setattr()` + `flush()` ORM update leave
   it in this state, since the ORM cannot locally know the server-computed
   value. `OrganizationResponse` is the only Phase 3 response schema that
   exposes `updated_at` on a mutating endpoint's return value; the
   subsequent synchronous Pydantic `model_validate()` triggered an implicit
   lazy-load attempt outside of an async context. **Fixed** by explicitly
   `await session.refresh(...)`-ing the object after both
   `OrganizationRepository.update()` and `.update_status()`, before it's
   returned for serialization. (Phase 2's equivalent response schemas —
   `UserResponse`, `MembershipResponse` — don't expose `updated_at`, so
   they were never at risk; this is noted in
   `docs/architecture/multi-tenancy.md` and the repository docstring so the
   same class of bug doesn't resurface if a future response schema adds
   the field.)
3. **Test premise vs. actual RBAC design (Phase 2 carryover, found during
   Phase 2 re-verification before Phase 3 began)**:
   `test_privilege_escalation_via_request_json_ignored` assumed a
   `HELP_DESK` user could self-update their profile; `HELP_DESK` correctly
   has no `user.update` permission per the documented role design, so 403
   was the correct behavior, not the 200 the original test asserted. Fixed
   by changing the test's actor to a user who legitimately holds
   `user.update`, which correctly exercises the mass-assignment protection
   the test is meant to prove (role/permission fields in the request body
   are ignored) rather than conflating it with permission enforcement
   (already covered elsewhere).

## 4. 403 vs. 404 Policy (documented per spec requirement)

See `docs/architecture/multi-tenancy.md` §6 for the full rationale;
summary: direct organization access by a non-member is `403
ORGANIZATION_ACCESS_DENIED` (organization existence isn't sensitive —
`SUPER_ADMIN` can list all of them); a nested resource ID belonging to a
*different* organization than the one already authorized in the path is
`404 NOT_FOUND` (avoids confirming a specific resource's existence in
another tenant). Both are explicitly tested in the same test module so
the distinction can't silently regress.

## 5. Row Level Security

**Deferred, not implemented** — see `docs/architecture/multi-tenancy.md`
§5 for the full rationale (test harness runs on SQLite, which has no
RLS/GUC support; a parallel PostgreSQL test harness would be
disproportionate scope for this phase). Application-layer filtering (every
tenant-scoped repository method requires `organization_id` explicitly) is
the enforced boundary today and is mandatory regardless of whether RLS is
ever added as a backstop. The schema keeps `organization_id` present and
indexed on every tenant table so RLS remains a purely additive change
later.

## 6. Known Limitations & Phase 4 Prerequisites

- **SSO / Enterprise IdP**: unchanged from Phase 2 — still deferred to
  Phase 12.
- **RLS**: deferred per §5 above; revisit once a PostgreSQL-backed test
  harness exists (likely alongside a broader test-infrastructure change,
  not scoped to any single domain phase).
- **Organization deletion**: not implemented — only `ACTIVE`/`SUSPENDED`/`DISABLED`
  status transitions exist. Hard-deleting an organization (and cascading
  every tenant-scoped row) is intentionally out of scope until a domain
  phase actually has data that would need cascading, to avoid designing
  the cascade behavior blind.
- **Real email delivery for invitations**: `EMAIL_PROVIDER=smtp` remains
  `NotImplementedError`, unchanged from Phase 2 — invitations work
  end-to-end today only via the console/dev provider (or the `null`
  provider in tests, which discards silently). Production deployment
  requires implementing the SMTP provider before invitations are usable
  outside local development.
- **Phase 4 (device inventory) must**: give every new table an
  `organization_id` FK + index from its first migration (not retrofitted
  later), use `get_for_organization(org_id, resource_id)`-shaped repository
  methods exclusively, and add a `tests/test_device_tenant_isolation.py`-style
  module before considering the domain "done" — mirroring exactly what
  this phase did for organizations/memberships.
