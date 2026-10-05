# Device Inventory & Management Foundation (Phase 4)

## 1. Status

Phase 4 implements **inventory** — the platform's record of what devices
exist and who they belong to. It does **not** implement Android Enterprise
or Apple MDM integration; those are future phases (see
`docs/architecture/android-enterprise.md`). Nothing in this document, or in
the `app/devices/` module, should be read as "the platform can control a
physical device" — it cannot, yet.

## 2. Inventory vs. Enrollment vs. Management

Three distinct concepts, deliberately kept separate in both the data model
and the language used everywhere in this codebase:

| Concept | Meaning | Phase 4 status |
|---|---|---|
| **Inventory** | "This device exists in the organization." | **Implemented.** A `Device` row, `organization_id`-scoped. |
| **Enrollment** | "This device has been registered with the MDM." | **Schema only.** `enrollment_status` defaults to `NOT_ENROLLED` and nothing in Phase 4 ever changes it. `enrolled_at` / `last_enrollment_attempt_at` columns exist and are reserved. |
| **Management** | "The MDM can actually control this device." | **Not started.** No code path in Phase 4 contacts, configures, locks, wipes, or otherwise communicates with a physical device. `POST /devices/{id}/block` changes a database column; it does not send any command anywhere. |

Creating a `Device` record, or a device having `status=ACTIVE`, means
exactly one thing: an administrator (or, in a later phase, an enrollment
flow) told the platform about a device. It is a database fact, not a claim
about the physical device's real-world state.

## 3. Device Model

`app/devices/models.py` — one table, `devices`, always scoped by
`organization_id` (NOT NULL, FK to `organizations.id`, indexed), following
the mandatory-tenant pattern established in Phase 3 for
`OrganizationMembership` (see `docs/architecture/multi-tenancy.md`).

Field groups:
- **Identity**: `device_identifier`, `serial_number`, `display_name`.
- **Hardware**: `manufacturer`, `model`, `model_identifier`.
- **Platform**: `platform`, `operating_system`, `os_version`,
  `security_patch_level` — closed enums where applicable (§4).
- **Classification**: `device_type`, `ownership_type`.
- **Lifecycle**: `status`, `enrollment_status` (§5).
- **Assignment**: `assigned_user_id` (§6).
- **Sensitive identifiers**: `imei_hash` / `imei_last4`,
  `wifi_mac_hash` / `wifi_mac_last4` — see `docs/security/device-security.md`.
- **Timestamps**: `last_seen_at`, `last_check_in_at`, `enrolled_at`,
  `last_enrollment_attempt_at`, plus the standard `created_at`/`updated_at`.
  All four device-specific timestamps are reserved columns — no Phase 4
  code path populates them, since there is no real check-in mechanism yet
  (§8).

## 4. Platform Abstraction

`platform`, `device_type`, and `ownership_type` are closed Python enums
(`app/devices/enums.py`), matching the `OrganizationStatus` /
`MembershipStatus` pattern from Phase 3 — never arbitrary client strings.
`OwnershipType` includes `CORPORATE`, `BYOD`, `COPE`, `COBO` per the product
spec, even though Phase 1–4 prioritize corporate-owned scenarios (see
`docs/architecture/android-enterprise.md` §7) — the enum reserves the space
now so it doesn't require a migration later.

The device inventory itself has zero Android/Apple-specific code. The
existing `PlatformManagementProvider` abstraction
(`app/platform/base/provider.py`) remains untouched and unimplemented —
Phase 4 does not add any concrete provider.

## 5. Device Lifecycle

```
                  +----------+
                  |  ACTIVE  |<---------------------+
                  +----------+                       |
                 /      |      \                     |
                v       v       v                    |
        +----------+ +------+                        |
        | INACTIVE | | LOST |  (generic PATCH,        |
        +----------+ +------+   non-privileged,        |
                 \      /        reversible among       |
                  \    /         these three)           |
                   \  /                                 |
                    v                                    |
              (from ACTIVE/INACTIVE/LOST)                |
                    |                                    |
                    v            device.block            |
               +---------+  <------------------+         |
               | BLOCKED |                      |         |
               +---------+  ------------------->+ device.unblock
                    |
                    | device.retire (from any non-RETIRED status,
                    v                including BLOCKED)
              +----------+
              | RETIRED  |   <- terminal. No endpoint, permission,
              +----------+      or PATCH body ever moves a device
                                 out of RETIRED.
```

Two tiers of transition, deliberately separated so a single permission
can't reach the destructive states:

1. **Generic** (`PATCH /devices/{id}`, `device.update`): `status`, if
   present in the request body, may only move the device among `ACTIVE`,
   `INACTIVE`, and `LOST` — validated in `DeviceService.update_device`
   against an explicit transition table. Requesting `BLOCKED` or `RETIRED`
   here is rejected with `409 INVALID_LIFECYCLE_TRANSITION`, never silently
   applied.
2. **Dedicated, separately permissioned** (`device.block`, `device.unblock`,
   `device.retire`): each is its own endpoint with its own permission, so
   e.g. HELP_DESK can hold `device.assign`/`device.unassign` without also
   being able to block or retire a device.

`RETIRED` is terminal by design (matches the spec's explicit requirement
that `RETIRED -> ACTIVE` must never be reachable via a normal PATCH) — see
§9 for why hard delete exists as a separate, more restricted escape hatch.

`enrollment_status` is not part of this graph at all in Phase 4 — it is
set once, at creation, to `NOT_ENROLLED`, and no endpoint changes it.

## 6. Device Assignment

A device may optionally be assigned to one user (`assigned_user_id`).
`DeviceService.assign_device` enforces, in order:

1. The device belongs to the caller's organization (guaranteed by
   `DeviceRepository.get_for_organization`).
2. The device is not `RETIRED` (`409 DEVICE_RETIRED`).
3. The target user has a membership in the **same** organization
   (`OrganizationMembership` lookup — `404 USER_NOT_IN_ORGANIZATION` if
   none exists; the 404 doesn't distinguish "user doesn't exist" from
   "user belongs to a different org").
4. That membership's status is `ACTIVE` (`400 MEMBERSHIP_NOT_ACTIVE` for
   `SUSPENDED`/`INVITED`/`REMOVED`).

Re-assigning a device simply replaces the previous assignee — there is no
separate "must unassign first" step. `unassign_device` has no status
restriction (a `RETIRED` device may still be unassigned, since that's a
cleanup action, not a new commitment of the device to someone).

## 7. Tenant Isolation

Devices are a **flat, non-nested** resource — `/api/v1/devices`, not
`/api/v1/organizations/{id}/devices` — so tenant context is resolved from
the `X-Organization-ID` header via
`app/organizations/dependencies.py::get_current_organization` (the exact
mechanism that file's own docstring reserves for "future (Phase 4+)
non-nested tenant-scoped endpoints"). `app/devices/dependencies.py` adds a
permission-gate wrapper around it without modifying any Phase 3 file.

Every device-repository method that touches a specific device requires
`organization_id` and filters on it
(`get_for_organization`, `update_for_organization`, `assign_for_organization`,
etc.) — there is no `get_by_id(device_id)` anywhere in
`app/repositories/device_repository.py`, by the same rule Phase 3
established for `MembershipRepository`. A device belonging to a different
organization is indistinguishable from a device that doesn't exist —
always `404 DEVICE_NOT_FOUND`, never a 403 that would confirm it exists
elsewhere. See `docs/security/device-security.md` §2 for the full IDOR
discussion.

## 8. Device Check-In

**Update (Phase 5):** the check-in mechanism deliberately left unbuilt
here now exists — see `docs/architecture/enrollment-and-policy.md` §5.
`POST /devices/{id}/heartbeat`, authenticated by a per-device credential
issued at enrollment (not an admin JWT), populates `last_seen_at` and
`last_check_in_at`, and connectivity state (`ONLINE`/`STALE`/`OFFLINE`/
`NEVER_CHECKED_IN`) is computed from `last_seen_at` at read time
(`app/devices/connectivity.py`) rather than stored. This was intentionally
deferred out of Phase 4 rather than faked with a timestamp-only endpoint
with no real credential behind it — see the original Phase 4 reasoning
below, which is why the deferral happened in the first place.

Original Phase 4 reasoning (kept for context): the model reserved
`last_seen_at` and `last_check_in_at` for a future, real check-in
mechanism. Phase 4 did not add a check-in endpoint at all — not even a
labeled internal/dev one — because there was nothing genuine for it to
represent yet: no enrolled device, no agent, no real communication
channel, and no per-device credential existed in that phase. Adding an
endpoint that just set a timestamp on request would have looked like a
working check-in mechanism without being one.

## 9. Delete vs. Retire

`retire_device` (STEP 22) is the intended path for normal operational
decommissioning — it's reversible in the sense that the row and its audit
history remain, just terminal in status. `DELETE /devices/{id}` performs a
genuine hard delete and exists only for cleanup of a mistakenly-created
record; it is gated by its own permission (`device.delete`), audited
(`DEVICE_DELETED`, including the device's display name in the audit
metadata since the row itself will be gone), and has no dependent Phase 4
records to cascade (device assignment is just a nullable FK on the device
row itself; there is no separate device-history table yet).

## 10. Future Android Integration

Nothing here changes the plan in `docs/architecture/android-enterprise.md`:
`PlatformManagementProvider` remains the abstraction boundary, Phase 4 adds
no Android-specific code, and before any real Android Enterprise API call
is made, Phase 5 must complete the mandatory research step documented
there (current official Google documentation, not memory or older
tutorials).

## 11. Future Apple Integration

No Apple-specific code, tables, or endpoints exist in Phase 4.
`app/platform/apple/` remains an empty placeholder package, exactly as
Phase 1 left it.

## 12. Management state (Phase 12)

`GET /devices` and `GET /devices/{id}` now also return
`management_state` (`"MANAGED"`/`"UNMANAGED"`), `provider_name`, and
`provider_device_id` — computed at read time from whether a
`ProviderDeviceLink` (Phase 8/10/11) exists, not a new stored column.
This is the "enrolled" (this section, Phase 5) vs. "managed" (a real
provider identity exists, Phase 9-11) distinction the top of this
document's module-docstring summary has always drawn conceptually, now
surfaced through the API. See `docs/architecture/phase-12.md` §4.
