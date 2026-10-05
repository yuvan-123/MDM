# Phase 4 — Device Inventory Security Review

## 1. Scope

Device inventory, lifecycle, assignment, and sensitive-identifier handling
introduced in Phase 4. Builds on
`docs/security/multi-tenancy-security-review.md` (tenant isolation) and
`docs/security/rbac.md` (permission model) — this review focuses on what's
new. Full design rationale in `docs/architecture/device-management.md` and
`docs/security/device-security.md`.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **IDOR — cross-tenant device reference** | Every device-repository method requires `organization_id` and filters on it (`DeviceRepository.get_for_organization` and friends) — no bare `get_by_id`. Cross-tenant device ID resolves to `404 DEVICE_NOT_FOUND`, never a 403 that confirms existence. | `tests/test_device_tenant_isolation.py` (GET/PATCH/DELETE/assign/block/retire) | PASS |
| **Forged tenant context** | `X-Organization-ID` header re-validated against real membership on every request via the unmodified Phase 3 `resolve_organization_context`; a header naming a non-member org is `403 ORGANIZATION_ACCESS_DENIED` before any device query runs. | `tests/test_device_tenant_isolation.py::test_forged_organization_header_rejected` | PASS |
| **Untrusted client-supplied `organization_id`** | No device request schema has an `organization_id` field — structurally impossible to supply one; org is always derived from the resolved context. | `tests/test_devices.py::test_create_device_ignores_client_supplied_organization_id` | PASS |
| **Cross-tenant device assignment** | `DeviceService.assign_device` independently verifies the target user has an `ACTIVE` membership in the *same* organization as the device, regardless of frontend filtering. | `tests/test_device_assignment.py::test_cannot_assign_device_to_user_in_different_organization` | PASS |
| **Assignment to a non-active member** | Same check rejects `SUSPENDED`/`INVITED`/`REMOVED` memberships with `400 MEMBERSHIP_NOT_ACTIVE`. | `tests/test_device_assignment.py::test_cannot_assign_device_to_suspended_member` | PASS |
| **Assignment of a retired device** | `assign_device` rejects any device with `status == RETIRED` before the membership checks even run. | `tests/test_device_assignment.py::test_cannot_assign_retired_device` | PASS |
| **Privilege escalation via lifecycle state** | `BLOCKED`/`RETIRED` are unreachable through the generic `device.update`-gated PATCH — only through their own dedicated, separately permissioned endpoints (`device.block`, `device.retire`). Generic PATCH validates against an explicit transition table; requesting `BLOCKED`/`RETIRED` there is `409 INVALID_LIFECYCLE_TRANSITION`. | `tests/test_device_lifecycle.py::test_update_device_cannot_jump_straight_to_blocked` (and the equivalent in `test_devices.py`) | PASS |
| **Terminal state bypass** | `RETIRED` has no outbound transitions in `_GENERIC_TRANSITIONS`/`_RETIRABLE_FROM`; re-retiring, blocking, unblocking, or generically patching a retired device's status all return `409`. | `tests/test_device_lifecycle.py::test_retire_is_terminal` | PASS |
| **RBAC — unauthorized mutation** | Every mutating endpoint gated by its own permission (`device.create/update/delete/assign/unassign/block/unblock/retire`) via `require_device_permission`, mirroring `require_organization_permission`; `403 PERMISSION_DENIED` audited on failure. | `tests/test_device_rbac.py` (HELP_DESK, AUDITOR negative cases) | PASS |
| **Sensitive data exposure** | IMEI/Wi-Fi MAC never stored in the clear (SHA-256 hash + last-4 fragment only, no reversible encryption); last-4 fragment itself gated by `device.view_sensitive`, `null` for callers without it. | `tests/test_device_sensitive_data.py::test_unprivileged_viewer_does_not_see_last4`, `test_imei_and_mac_never_returned_in_full` | PASS |
| **Sensitive data access — audit trail** | `DEVICE_SENSITIVE_DATA_VIEWED` logged on every detail-view (`GET /devices/{id}`) that actually returns a populated last-4 fragment; not logged for unprivileged viewers or for list views (avoids audit-log flooding on ordinary browsing). Fragment value itself never placed in audit metadata. | `tests/test_device_sensitive_data.py::test_sensitive_view_is_audited`, `test_unprivileged_viewer_does_not_trigger_sensitive_audit` | PASS |
| **Sensitive-field search exposure** | `device.search` excludes `imei_hash`/`imei_last4`/`wifi_mac_hash`/`wifi_mac_last4` from the searchable field set entirely — not just from the response. | `app/repositories/device_repository.py::_apply_filters` (code review; no dedicated negative test — see §4) | PASS |
| **Organization-scoped duplicate identifiers** | `device_identifier`, `serial_number`, and `imei_hash` uniqueness is enforced per-organization (`UniqueConstraint(organization_id, field)`), not globally — the same serial number is valid in two different organizations. | `tests/test_devices.py::test_same_serial_number_allowed_across_organizations`, `test_duplicate_device_identifier_rejected` | PASS |
| **Disabled/suspended organization access** | Reuses Phase 3's unmodified `resolve_organization_context` org-status check — a `DISABLED` organization's members lose device access (`403 ORGANIZATION_SUSPENDED`) exactly like every other Phase 3 resource. | `tests/test_device_tenant_isolation.py::test_disabled_organization_denies_device_access` | PASS |
| **Unbounded/expensive queries** | Filtering, search, and sort all happen in SQL (`WHERE`/`ORDER BY`/`LIMIT`/`OFFSET`); sort column resolved through an explicit whitelist; `page_size` capped at 100. | `tests/test_devices.py::test_pagination_rejects_excessive_page_size` | PASS |
| **No fake device management** | Lifecycle endpoints (`block`/`unblock`/`retire`) only ever change the `devices.status` column; no code path calls an external platform API or claims real device control. Documented explicitly in endpoint docstrings and `docs/security/device-security.md` §6. | Code review (`app/devices/router.py`, `app/devices/service.py` — no HTTP client, no platform-provider import) | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **Dead lifecycle-transition code, unreachable from any endpoint.**
   `DeviceService` originally implemented a fully correct
   `update_device_status()` method (ACTIVE/INACTIVE/LOST transition
   validation) as a *separate* method from `update_device()`, but
   `UpdateDeviceRequest` had no `status` field and the router's generic
   PATCH endpoint only ever called `update_device()` — so the transition
   logic was never invoked by any HTTP request, and a `PATCH` with
   `{"status": "LOST"}` silently did nothing (Pydantic ignores unknown
   fields by default). Caught by
   `tests/test_device_lifecycle.py::test_generic_update_allows_active_inactive_lost`
   asserting `LOST` after the PATCH and getting `ACTIVE` back. **Fixed**
   by adding `status: DeviceStatus | None` to `UpdateDeviceRequest` and
   folding the transition validation directly into `update_device()`
   (single fetch, single validation, single repository write, single
   audit entry) rather than keeping two separate code paths that could
   drift out of sync; the now-redundant standalone
   `update_device_status()` method was removed rather than left as dead
   code. This also required updating three tests that had been written
   against the (incorrect) assumption that `status` was silently ignored
   by the schema — those assertions now correctly expect
   `409 INVALID_LIFECYCLE_TRANSITION` for an invalid generic transition,
   matching STEP 9's requirement that BLOCKED/RETIRED stay unreachable
   through the generic path.
2. **mypy strict — insufficiently generic repository query helper.**
   `DeviceRepository._apply_filters` was initially typed to accept/return
   `Select[tuple[Device]]` only, but it's also used to build the `COUNT(*)`
   query in `count_for_organization`, whose `Select` has a different row
   type (`tuple[int]`). mypy caught the mismatch (`incompatible type
   "Select[tuple[int]]"`) before any test run. **Fixed** by making the
   method generic over the row-tuple type (`TypeVar` bound to
   `tuple[object, ...]`) instead of loosening it to `Any`, preserving the
   type-checking value of the method for its primary (entity-query) use
   while allowing the count-query reuse.

## 4. Known Gaps / Deferred to Phase 5+

- No dedicated negative test asserts that searching for a full IMEI or
  MAC fragment returns no results (the field is simply absent from the
  search `WHERE` clause, verified by code review rather than a runtime
  test — a low-risk gap since there's no code path that could
  accidentally include it).
- Row Level Security remains deferred (SQLite test harness, per the
  Phase 3 limitation this phase inherits unchanged) — application-layer
  `organization_id` filtering in every repository method is the enforced
  boundary, exactly as documented for Phase 3.
- Docker/Postgres was not available in this development environment and
  was not verified for this phase either — the migration
  (`0004_phase4_devices.py`) was written against the same patterns as
  `0003_phase3_multi_tenancy.py` (which was itself never run against real
  Postgres) but has not been executed against a live database.
- The pre-existing absence of any `session.commit()` call anywhere in
  `app/` (repositories only `flush()`) was not introduced by, or fixed
  in, Phase 4 — it is the established pattern from Phase 1–3 and out of
  this phase's scope to change. Whether writes persist against a real
  Postgres connection in production remains unverified, same as every
  prior phase.
