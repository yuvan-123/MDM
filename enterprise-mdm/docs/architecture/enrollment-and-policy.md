# Enrollment, Device Lifecycle, and Policy Management (Phase 5)

Builds directly on `docs/architecture/device-management.md` (Phase 4
inventory foundation) and `docs/architecture/multi-tenancy.md` (tenant
isolation). This document covers what Phase 5 adds: real enrollment, a
device heartbeat, device groups, a versioned policy engine, and a
compliance evaluation engine.

## 1. Scope and Non-Goals

Phase 5 makes enrollment **real** in the sense that a device now gets a
genuine, checkable credential and a real state machine — but it is still
not Android Enterprise or Apple MDM integration. `PlatformManagementProvider`
(`app/platform/base/provider.py`) remains an unimplemented abstraction; no
Android Management API call, no APNs push, and no real device command is
issued anywhere in this phase. "Enrolled" here means "the backend has a
verified device identity and an authenticated check-in channel," not "the
MDM can push policy to the physical device" — see §7 for exactly what a
compliance evaluation does and does not do.

## 2. Enrollment Data Model

Three tables, deliberately kept separate:

| Table | Represents |
|---|---|
| `enrollment_tokens` | The credential an admin generates and hands to a device (or its user). |
| `device_enrollments` | One record per enrollment attempt, 1:1 with the `Device` it produces. The authoritative state machine + audit trail for *how* a specific device came to be enrolled. |
| `devices` (extended) | The resulting inventory row — `enrollment_id` points back to its `device_enrollments` row; `enrollment_status` is a denormalized copy of `device_enrollments.status`, kept in sync by `EnrollmentService`, so device list/filter queries never need to join enrollment history. |

`EnrollmentToken.token_hash` and `Device.device_secret_hash` both follow
the exact raw-value-never-stored pattern established for
`OrganizationInvitation.token_hash` in Phase 3: SHA-256 hash persisted,
raw value returned exactly once, at creation, never retrievable again
(see `app/enrollment/identity.py`).

## 3. Enrollment State Machine

`app/devices/enums.py::EnrollmentStatus` (on `Device`, mirrored from
`app/enrollment/models.py::DeviceEnrollmentStatus`, the authoritative
version on `DeviceEnrollment`):

```
NOT_ENROLLED           (Device created manually, Phase 4 style — no
                         DeviceEnrollment row at all)

        (self-registration via POST /enrollment/register)
                |
                v
            PENDING  --------reject------->  REJECTED   (terminal)
                |
           approve (POST /devices/{id}/enrollment/approve)
                |
                v
            ENROLLED  -------revoke------->  REVOKED    (terminal;
                                                           clears
                                                           device_secret_hash)

  (require_approval=False on the token skips PENDING entirely —
   registration goes straight to ENROLLED)
```

`APPROVED` and `EXPIRED` exist as enum values (spec-required) but are
handled differently:
- `APPROVED` is **reserved, never set** by any Phase 5 code path. The
  spec's "pending → approved → enrolled" chain implies a two-step
  confirmation where a real device agent completes a follow-up call after
  admin approval — no such agent exists yet, so `approve_enrollment`
  transitions `PENDING` directly to `ENROLLED` in one step, exactly the
  same way Phase 4 documented `APPROVED`-adjacent reserved states. This
  is a documented interpretation, not an oversight.
- `EXPIRED` **is** reachable, but lazily: `EnrollmentToken` status is
  computed at read/use time (`app/enrollment/service.py::_effective_token_status`)
  from `expires_at`/`use_count` rather than a background job, matching
  the same "don't rely on a stored status field alone" principle Phase 5
  applies to device connectivity (§5).

## 4. Enrollment Token Rules

- **Cryptographically random**: `secrets.token_urlsafe(32)`.
- **Hashed at rest**: SHA-256, never the raw value.
- **Expiring**: `expires_in_hours` (1–2160, i.e. up to 90 days), required
  on every token.
- **Single-use by default**: `max_uses` defaults to 1; configurable up to
  10,000 for bulk-enrollment scenarios. A token that reaches `max_uses`
  flips to `EXHAUSTED` on the use that consumes it.
- **Revocable**: `DELETE /enrollment/tokens/{id}` sets `status=REVOKED`
  (not a hard delete — `DeviceEnrollment` rows reference it and must
  remain intact for history).
- **Org-scoped**: created under the caller's resolved
  `OrganizationContext.organization_id`; a device registered with a valid
  token from organization B always lands in organization B, regardless of
  who is making the (unauthenticated) registration call — there is no
  "requesting organization" on this endpoint at all, only the token's own
  organization (verified in `tests/test_phase5_tenant_isolation.py`).
- **Optionally scoped further**: `target_user_id` (pre-assigns the
  resulting device), `allowed_platform` / `allowed_device_type` (rejects
  registration attempts that don't match — `PLATFORM_NOT_ALLOWED` /
  `DEVICE_TYPE_NOT_ALLOWED`).
- **Never logged**: the raw token appears in exactly one place — the
  `CreateEnrollmentTokenResponse.token` field, once. It is never written
  to a log line, an audit event's metadata, or any other response.

## 5. Device Credential and Heartbeat

A real device has no platform-user JWT, so heartbeat uses a distinct,
minimally-scoped credential rather than piggy-backing on admin auth:

- At enrollment completion (registration, or approval if the token
  required review), a random secret is generated
  (`app/enrollment/identity.py::generate_device_secret`), its SHA-256
  hash stored on `Device.device_secret_hash`, and the raw value returned
  exactly once in the registration response.
- `POST /devices/{id}/heartbeat` authenticates via
  `X-Device-Id` / `X-Device-Secret` headers
  (`app/enrollment/dependencies.py::get_enrolled_device`), independent of
  the CSRF/session-cookie admin auth path entirely.
- The dependency checks, in one pass, returning the **same** generic
  `401 DEVICE_CREDENTIAL_INVALID` for every failure so a caller can't
  distinguish one failure mode from another: unknown device ID, wrong
  secret, `enrollment_status != ENROLLED`, or `status in (RETIRED,
  BLOCKED)`.
- The path's `{device_id}` must match the authenticated credential's own
  device — a valid credential cannot be used to heartbeat as a different
  device ID (defense-in-depth beyond the credential check itself).
- Revoking a device's enrollment (§3) immediately clears
  `device_secret_hash`, so a revoked device's old credential stops
  authenticating instantly, not just logically.

**Connectivity state** (`ConnectivityState`: `ONLINE` / `STALE` /
`OFFLINE` / `NEVER_CHECKED_IN`) is computed at read time from
`last_seen_at` (`app/devices/connectivity.py`) — never stored as a
column, matching the spec's explicit "do not rely only on a manually
supplied status field; use timestamps and server-side logic." Thresholds
(`device_online_threshold_minutes=5`, `device_stale_threshold_hours=24`)
are `Settings` fields, not hardcoded.

## 6. Device Groups

`device_groups` / `device_group_members` — a plain org-scoped container +
many-to-many join, no per-membership status (unlike
`OrganizationMembership`; a device either is or isn't in a group).
`DeviceGroupService.add_device` independently re-verifies both the group
and the device belong to the caller's organization before allowing
membership — the same "never trust, always re-derive" rule as device
assignment (Phase 4). Groups exist for policy assignment scope (§7) and
future bulk actions/reporting.

## 7. Policy Engine

### 7.1 Configuration Shape

A policy's `configuration` is **one JSON blob per version**
(`app/policies/schemas.py::PolicyConfiguration`), not a column per
control. Every field in every category (`password`, `device_security`,
`application`, `network`, `compliance`) is optional — an absent field
means "this policy doesn't constrain that setting," never "require the
default." This is what makes the engine extensible without a
schema-breaking migration every time a new control is added: a new rule
in `app/compliance/rules.py` simply reports `UNKNOWN` for any
`PolicyVersion` that predates it.

### 7.2 Versioning

`Policy` (mutable pointer: `name`, `description`, `status`,
`current_version_id`) + `PolicyVersion` (immutable history). Creating a
policy always creates version 1 alongside it; updating `configuration`
always creates a brand-new `PolicyVersion` and repoints
`current_version_id` — an existing version's `configuration` is **never**
mutated in place. `Policy.current_version_id` and
`PolicyVersion.policy_id` are a genuine circular FK pair, resolved in the
migration the same way `Device.enrollment_id` / `DeviceEnrollment.device_id`
are: create both tables with the back-reference nullable, add the FK
constraint via a separate `ALTER TABLE` once both tables exist.

`status`: `DRAFT -> ACTIVE -> DRAFT` (unpublish/republish) via
`PATCH /policies/{id}`; `ARCHIVED` is reachable only via
`DELETE /policies/{id}` (a soft delete — blocked with
`409 POLICY_HAS_ASSIGNMENTS` while any assignment still references the
policy, since history/auditability must not silently break).

### 7.3 Assignment and Precedence

A policy is assigned to exactly one of four scope types
(`PolicyScopeType`: `ORGANIZATION`, `USER`, `DEVICE_GROUP`, `DEVICE`) via
`POST /policies/{id}/assign` — `scope_id` is validated against the
correct table for that scope type in `PolicyService._validate_scope`
(the org itself, an org member, a device group in the org, or a device in
the org — never trusted blindly).

Precedence, implemented in `app/policies/service.py::PolicyResolutionService`:

```
DEVICE  >  DEVICE_GROUP  >  USER  >  ORGANIZATION
```

`resolve_for_device` checks each level in order and stops at the first
level with an **ACTIVE**-status policy assigned — a `DRAFT` policy can be
assigned ahead of time (to stage it) but is skipped during resolution
until published. If a device belongs to multiple groups that each have a
policy assigned, the most-recently-assigned wins (documented tie-break,
not an error condition). This existing precedence model is used as-is by
the compliance engine (§7) — there is no separate, competing precedence
implementation anywhere else in the codebase.

## 8. Compliance Evaluation

### 8.1 Rule Registry

`app/compliance/rules.py::RULES` is a plain list of functions,
`(device, config) -> RuleResult | None`. Returning `None` means the rule
doesn't apply (the relevant config section wasn't set) and is excluded
from the result entirely. Adding a check is adding a function and
appending it to the list — the evaluation loop in
`ComplianceService.evaluate_device` never changes.

Implemented rules (STEP 6's "device security" category, mapped onto the
`is_encrypted` / `has_screen_lock` / `is_rooted` / `os_version` /
`client_version` fields devices report via heartbeat):
`minimum_os_version`, `require_encryption`, `require_screen_lock`,
`block_rooted_jailbroken`, `minimum_client_version`.

`password`, `application`, and `network` policy categories are modeled in
`PolicyConfiguration` (so policies can already store that configuration)
but have **no compliance rules evaluating them yet** — there's nothing on
the `Device` model to check a password policy or installed-app list
against without real platform integration. This is the same
"foundation now, real enforcement later" split Phase 4 applied to
enrollment — the schema exists, the check does not yet.

### 8.2 Overall Status

- Any rule `FAIL` → `NON_COMPLIANT`.
- No `FAIL`, at least one `UNKNOWN` → `UNKNOWN`.
- No rules failed or unknown (including zero rules applying, or **no
  policy resolved at all** for the device) → `COMPLIANT`... except: no
  resolved policy is reported as `UNKNOWN` with a single
  `NO_POLICY_ASSIGNED` detail entry, not `COMPLIANT` — "there's no
  baseline to measure against" is treated as insufficient information,
  distinct from "there's an active policy but this section wasn't
  configured" (which is trivially compliant).

### 8.3 Storage

Same "current + history" split as Policy/PolicyVersion:
`device_compliance` (one row per device, upserted — cheap dashboard
queries) and `compliance_results` (insert-only, one row per evaluation
run, `evaluated_by` nullable for future automatic/system-triggered runs).

### 8.4 API

`GET /compliance/devices/{id}`, `GET /compliance/summary`,
`GET /compliance/non-compliant`, `POST /compliance/evaluate/{id}` — all
tenant-scoped via the same header-based `OrganizationContext` pattern as
every other Phase 4/5 module. Evaluation is **on-demand only** in Phase
5 — nothing runs it automatically on a schedule or on every heartbeat;
an admin (or, later, an automated job) must call
`POST /compliance/evaluate/{id}` explicitly. **Superseded by Phase 7**:
a device heartbeat now enqueues a `COMPLIANCE_EVALUATION` background job
automatically — see `docs/architecture/background-jobs.md` §5.

**Phase 13** added `RuleResult.expected`/`.actual` (populated by every
rule in §8.1's registry) and a normalized `PolicyViolation` per failing
`(device, policy, rule)` with its own remediation lifecycle — see
`docs/architecture/phase-13.md`. `GET/POST /compliance/violations*`
extends this section's API without replacing it.

## 9. API Vocabulary Mapping (Phase 4 vs. Phase 5 Spec Language)

The Phase 5 prompt names device lifecycle actions ("suspend", "restore")
that already exist under different names from Phase 4. Per the prompt's
own instruction to follow existing convention rather than blindly
duplicate paths, no new endpoints were added for these — see
`app/enrollment/router.py`'s module docstring for the authoritative list:

| Spec vocabulary | Actual endpoint | Why |
|---|---|---|
| "Suspend device" | `POST /devices/{id}/block` (Phase 4) | Same concept, different word choice between the two prompts. |
| "Re-enable device" | `POST /devices/{id}/unblock` (Phase 4) | Same concept. |
| "Enroll device" | Outcome of `POST /devices/{id}/enrollment/approve`, or automatic on registration if the token has `require_approval=False` | No separate manual "enroll" click needed. |
| "Rename device" / "Update metadata" | `PATCH /devices/{id}` (Phase 4, already supports `display_name` and every other descriptive field) | Already covered. |
| "Revoke device" (Phase 5 sense) | `POST /devices/{id}/enrollment/revoke` | Distinct from Phase 4's `block` — this revokes the *enrollment credential* specifically, not the inventory lifecycle status. |

## 10. Future Phases

Nothing here changes the Android/Apple integration plan in
`docs/architecture/android-enterprise.md`. Before any real platform
command is wired to a policy or a lifecycle action, that phase must
independently research current official platform documentation, exactly
as already required there.

**Phase 11 added** real provider-side enrollment provisioning
(`EnrollmentToken.provision_via_provider`, `POST /enrollment/register`'s
optional `provider_device_id`/`apple_push_token`, and automatic
ProviderDeviceLink creation for both the direct-registration and
Android-webhook-checkin paths) on top of this foundation — see
`docs/architecture/phase-11.md`. This section's token/registration/
approve-reject-revoke model is otherwise unchanged.
