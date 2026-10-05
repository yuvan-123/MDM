# Phase 5 — Enrollment & Policy Security Review

## 1. Scope

Device enrollment, heartbeat, device groups, policy management, and
compliance evaluation introduced in Phase 5. Builds on
`docs/security/multi-tenancy-security-review.md` (tenant isolation),
`docs/security/rbac.md` (permission model), and
`docs/security/device-security.md` (Phase 4 device inventory security) —
this review focuses on what's new. Full design rationale in
`docs/architecture/enrollment-and-policy.md`.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Enrollment token security** | `secrets.token_urlsafe(32)`, SHA-256 hash stored (never raw), mandatory `expires_in_hours`, `max_uses` (default 1), revocable (`status=REVOKED`, not hard-deleted). | `tests/test_enrollment.py::test_expired_token_rejected`, `test_revoked_token_rejected`, `test_single_use_token_cannot_be_reused` | PASS |
| **Token org-scoping** | A token is always created under the caller's `OrganizationContext.organization_id`; registration derives the resulting device's org from the token record, never from the (unauthenticated) request. | `tests/test_phase5_tenant_isolation.py::test_org_a_cannot_use_org_b_enrollment_token` | PASS |
| **Cross-tenant token access** | `EnrollmentTokenRepository` requires `organization_id` on every admin-facing lookup (list, revoke) — only the unauthenticated `get_by_token_hash` is bare, by necessity, exactly like `DeviceRepository.get_by_id_unscoped`. | `tests/test_phase5_tenant_isolation.py::test_org_a_cannot_revoke_org_b_enrollment_token` | PASS |
| **Device credential security** | Random secret (`secrets.token_urlsafe(32)`) issued once at enrollment, SHA-256 hash stored on `Device.device_secret_hash`, raw value never persisted or logged. Immediately cleared on enrollment revocation. | `tests/test_enrollment.py::test_revoke_enrolled_device_clears_credential` | PASS |
| **Device credential channel isolation** | Heartbeat authenticates via `X-Device-Id`/`X-Device-Secret`, entirely independent of the admin JWT/CSRF path; a device credential cannot be used to call any admin endpoint (no code path accepts it there) and an admin session cannot heartbeat (no code path accepts a JWT on that endpoint). | `app/enrollment/dependencies.py::get_enrolled_device` (code review) + `tests/test_heartbeat.py` | PASS |
| **Device credential — generic failure response** | Unknown device, wrong secret, not-yet-`ENROLLED`, `BLOCKED`, and `RETIRED` all return the identical `401 DEVICE_CREDENTIAL_INVALID` — no oracle for enumerating valid device IDs. | `tests/test_heartbeat.py::test_heartbeat_with_wrong_secret_rejected`, `test_heartbeat_for_unknown_device_rejected`, `test_pending_enrollment_device_cannot_heartbeat` | PASS |
| **Device credential — path/credential binding** | The heartbeat path's `{device_id}` must equal the authenticated credential's own device; a valid credential for device A cannot be used to heartbeat "as" device B. | `tests/test_heartbeat.py::test_heartbeat_device_id_header_must_match_path` | PASS |
| **CSRF on unauthenticated registration** | `/api/v1/enrollment/register` added to `CSRFMiddleware.EXEMPT_PATHS`, same trust model and same bug class as Phase 3's `invitations/accept` fix — an admin testing the flow from their own browser won't trip CSRF on their unrelated session cookie. | `app/auth/middleware.py::EXEMPT_PATHS` (code review, mirrors an already-tested Phase 3 pattern) | PASS |
| **Cross-tenant device-enrollment actions** | `approve`/`reject`/`revoke` all resolve the `DeviceEnrollment` via `organization_id`-scoped lookup; a device in a different org is `404`. | `tests/test_phase5_tenant_isolation.py::test_org_a_cannot_approve_org_b_device_enrollment` | PASS |
| **RBAC — enrollment/groups/policy/compliance** | New permissions (`enrollment_token.*`, `device_group.*`, `policy.unassign`, `compliance.evaluate`) integrated into the existing `app/rbac/permissions.py` system, additive-only role grants. | `tests/test_enrollment.py::test_help_desk_cannot_create_token_without_permission`, `tests/test_device_groups.py::test_help_desk_cannot_create_group`, `tests/test_policies.py::test_help_desk_cannot_create_policy`, `tests/test_compliance.py::test_help_desk_cannot_trigger_evaluation` | PASS |
| **Cross-tenant device groups** | `DeviceGroupRepository` requires `organization_id` on every method; adding a cross-tenant device to a group is `404 DEVICE_NOT_FOUND` (device lookup itself is org-scoped). | `tests/test_device_groups.py::test_cross_tenant_group_access_denied`, `test_cannot_add_cross_tenant_device_to_group` | PASS |
| **Cross-tenant policies** | `PolicyRepository`/`PolicyAssignmentRepository` require `organization_id` on every method; `_validate_scope` independently re-verifies a USER/DEVICE_GROUP/DEVICE scope_id belongs to the caller's own organization before allowing assignment — an `ORGANIZATION` scope_id must literally equal the caller's own org ID. | `tests/test_policies.py::test_cross_tenant_policy_access_denied`, `test_cannot_assign_to_foreign_organization_scope`, `test_cannot_assign_to_device_in_other_organization` | PASS |
| **Cross-tenant compliance** | `ComplianceService` resolves the device via the same org-scoped `DeviceRepository.get_for_organization` as every other device operation — cross-tenant device ID is `404`. | `tests/test_compliance.py::test_cross_tenant_compliance_access_denied` | PASS |
| **Forged `X-Organization-ID` — new modules** | Every new Phase 5 router uses the same `require_current_organization_permission` (header-based, re-validates real membership every request) as Phase 4's devices module — not a parallel, possibly-weaker check. | `tests/test_phase5_tenant_isolation.py::test_forged_organization_header_rejected_for_new_modules` (device-groups, policies, enrollment/tokens) | PASS |
| **Untrusted `organization_id`/`user_id`/device ownership** | No Phase 5 request schema accepts `organization_id`; policy scope validation independently re-derives whether a `user_id`/`device_id`/`group_id` belongs to the caller's org rather than trusting the client's claim. | Code review (`app/policies/service.py::_validate_scope`) + tests above | PASS |
| **Privilege escalation via policy status** | `PolicyStatus.ARCHIVED` is reachable only through `DELETE`, never through `PATCH ... {"status": "ARCHIVED"}` — the update transition table (`_STATUS_TRANSITIONS`) only permits `DRAFT <-> ACTIVE`. | `tests/test_policies.py::test_cannot_archive_via_patch` | PASS |
| **Policy history integrity** | `DELETE /policies/{id}` is blocked (`409 POLICY_HAS_ASSIGNMENTS`) while any assignment references the policy; editing a policy's configuration always creates a new `PolicyVersion` rather than mutating an existing one, preserving compliance-evaluation history's meaning. | `tests/test_policies.py::test_delete_blocked_while_assigned`, `test_update_configuration_creates_new_version_without_mutating_old` | PASS |
| **Invalid enrollment-state transitions** | `approve`/`reject` only fire from `PENDING`; `revoke` only fires from `ENROLLED` — each other combination is `409 INVALID_ENROLLMENT_TRANSITION`. | `tests/test_enrollment.py::test_cannot_approve_already_enrolled_device` | PASS |
| **No fake platform enforcement** | Compliance evaluation only ever reads device-reported fields already in the database (via heartbeat) and writes a `DeviceCompliance`/`ComplianceResult` row — no code path in `app/compliance/` calls an external platform API or issues a device command. `PlatformManagementProvider` remains untouched. | Code review (`app/compliance/service.py`, `app/compliance/rules.py` — no HTTP client, no platform-provider import) | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

Caught by static analysis (ruff/mypy strict) before any test ran — see
each fix's commit-equivalent in the corresponding source file:

1. **Repository query helper insufficiently generic for reuse across
   entity/count queries and heterogeneous return types**, in three
   repositories (`DeviceGroupRepository`, `PolicyRepository`,
   `ComplianceRepository`) — the same class of issue Phase 4 hit once in
   `DeviceRepository._apply_filters`. mypy strict caught each instance
   (`Unused "type: ignore"` after a first attempt at a loose fix, or an
   outright type mismatch) before any test run; fixed by typing the
   affected fields/parameters precisely (`datetime` instead of `object`,
   `list[dict[str, Any]]` instead of `dict[str, Any]` for the JSON
   `details` columns) rather than suppressing the check.
2. **`DeviceCompliance.details` / `ComplianceResult.details` were
   initially typed as `dict[str, Any] | None`** (copied from the
   `AuditLog.event_metadata` pattern) but the actual data written is a
   JSON **array** of per-rule results, not an object — mypy caught the
   mismatch when `ComplianceService.evaluate_device` tried to pass a
   `list[...]` into a `dict[...]`-typed parameter. Fixed by correcting
   the model annotation to `list[dict[str, Any]] | None`, matching what
   is actually stored.

Caught only by running the actual test suite (not by static analysis —
these are runtime/data behaviors mypy has no visibility into):

3. **Enrollment token expiry check crashed with `TypeError: can't compare
   offset-naive and offset-aware datetimes`** on every registration
   attempt. Root cause: SQLite (the test harness) silently drops `tzinfo`
   on round-trip even for `DateTime(timezone=True)` columns — a value
   written as `datetime.now(tz=UTC)` comes back naive after a flush/refresh
   cycle. `_effective_token_status` compared that naive value directly
   against a fresh `datetime.now(tz=UTC)`. This is a **known, previously
   solved problem in this exact codebase** — `app/auth/models.py::Session.is_active`
   already carries the fix (`if expires.tzinfo is None: expires = expires.replace(tzinfo=UTC)`)
   for the identical situation with refresh-token expiry. The Phase 5 code
   was written without noticing that precedent. **Fixed** by applying the
   same normalization in `_effective_token_status`
   (`app/enrollment/service.py`) and in `compute_connectivity_state`
   (`app/devices/connectivity.py`, same root cause, found by the same test
   run). Real-world impact if shipped: every device registration and every
   heartbeat would have crashed with a 500 the first time either function
   ran against the real (Postgres-backed) deployment path that actually
   exercises stored, re-read timestamps rather than in-memory ones — this
   was not a SQLite-only cosmetic issue, it reflects genuinely different
   driver behavior that the fix must handle regardless of database engine.
4. **`Policy.current_version` was `None` immediately after
   `create_policy`/`update_policy`**, causing
   `PolicyResponse.current_version` to serialize as `null` right after
   creating a policy with its very first version. Root cause:
   `PolicyRepository.update_for_organization` sets `current_version_id`
   and calls `session.refresh()`, but `refresh()` only reloads the
   instance's own columns — it does not populate a `lazy="noload"`
   relationship that was never loaded in the first place, so
   `policy.current_version` stayed `None` even though the FK column was
   correctly set. **Fixed** by assigning the relationship directly from
   the `PolicyVersion` object already held in memory
   (`policy.current_version = version`) immediately after the FK update,
   avoiding both the stale-`None` bug and an unnecessary extra query.
5. **`SAWarning: Can't sort tables for DROP` on every test-suite
   teardown** — `Base.metadata.drop_all()` (used by the SQLite test
   fixture) couldn't topologically order `devices`/`device_enrollments`
   and `policies`/`policy_versions` because each pair has a genuine
   circular foreign key (Device.enrollment_id <-> DeviceEnrollment.device_id;
   Policy.current_version_id <-> PolicyVersion.policy_id). The migration
   already resolves this correctly (creates the second-direction FK via a
   separate `ALTER TABLE` once both tables exist — see
   `0005_phase5_enrollment_policy.py`), but the SQLAlchemy `Base.metadata`
   object itself didn't have the equivalent hint. **Fixed** by adding
   `use_alter=True` to both circular `ForeignKey(...)` declarations in
   `app/devices/models.py` and `app/policies/models.py`, telling
   SQLAlchemy explicitly that these are known, intentionally-delayed
   constraints — the same fact the migration already encodes, now encoded
   in the ORM layer too so `create_all`/`drop_all` (used only by tests)
   don't have to guess.

## 4. Known Gaps / Deferred to Phase 6+

- **No dedicated negative test for a genuinely time-expired token via
  real wall-clock passage** — `expires_in_hours` has a minimum of 1, so
  `tests/test_enrollment.py::test_expired_token_rejected` backdates the
  persisted `expires_at` directly via the repository rather than waiting;
  this still exercises the real lazy-expiry check in
  `EnrollmentService.register_device`, just not through elapsed real
  time.
- **Compliance evaluation is on-demand only** — nothing runs it
  automatically on heartbeat or on a schedule. A device can report
  non-compliant health data via heartbeat and remain marked `UNKNOWN`
  (its previous evaluation, if any, unchanged) until an admin explicitly
  calls `POST /compliance/evaluate/{id}`. This is a deliberate Phase 5
  scope boundary, not an oversight — automatic/scheduled evaluation
  needs a background job mechanism this codebase does not have yet.
- **`password`/`application`/`network` policy categories have no
  compliance rules evaluating them** — the configuration schema exists
  (STEP 6 requires the categories to exist), but there is nothing on the
  `Device` model yet to check a password policy or an installed-app list
  against without real platform integration. Documented explicitly in
  `docs/architecture/enrollment-and-policy.md` §8.1, not silently absent.
- **Device-group precedence tie-break (multiple groups, each with a
  policy) uses "most recently assigned wins"** — a documented, simple
  rule, not validated against any external product spec for "correct"
  multi-group precedence, since none was given.
- Row Level Security remains deferred (SQLite test harness), unchanged
  from Phase 3/4.
- Docker/Postgres was not available in this development environment and
  was not verified for this phase either — migration
  `0005_phase5_enrollment_policy.py` was written against the same
  patterns as `0004_phase4_devices.py` (itself never run against real
  Postgres) but has not been executed against a live database.
- The pre-existing absence of any `session.commit()` call anywhere in
  `app/` (repositories only `flush()`) was not introduced by, or fixed
  in, Phase 5 — unchanged from every prior phase.
