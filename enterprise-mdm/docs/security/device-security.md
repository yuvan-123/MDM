# Device Security (Phase 4)

Builds on `docs/security/multi-tenancy-security-review.md` (tenant
isolation) and `docs/security/rbac.md` (permission model). This document
covers the device-specific additions.

## 1. RBAC

New permissions, all `resource.action` strings integrated into the
**existing** permission system (`app/rbac/permissions.py`) — no parallel
device authorization mechanism:

| Permission | Purpose |
|---|---|
| `device.read` | View device inventory (pre-existing from Phase 2 scaffolding) |
| `device.create` | Create inventory records |
| `device.update` | Descriptive-field updates + ACTIVE/INACTIVE/LOST transitions |
| `device.delete` | Hard delete (STEP 22 — restricted, prefer retire) |
| `device.assign` / `device.unassign` | Change device assignment |
| `device.block` / `device.unblock` | BLOCKED lifecycle transition |
| `device.retire` | RETIRED lifecycle transition (terminal) |
| `device.view_sensitive` | See the last-4 fragment of IMEI / Wi-Fi MAC (§4) |

`device.enroll`, `device.lock`, `device.wipe` already existed in
`app/rbac/permissions.py` from earlier scaffolding — Phase 4 does not use
or grant new meaning to them; they remain reserved for the real
Android/Apple integration phases where they'll gate an actual device
command, not an inventory field change.

Role changes (additive only — no existing grant was removed):

| Role | Added Phase 4 permissions |
|---|---|
| `ORGANIZATION_ADMIN` | `device.assign`, `device.unassign`, `device.block`, `device.unblock`, `device.retire`, `device.view_sensitive` (already had `device.read/create/update/delete`) |
| `SECURITY_ADMIN` | `device.block`, `device.unblock`, `device.view_sensitive` (already had `device.read`) |
| `HELP_DESK` | `device.assign`, `device.unassign` (already had `device.read`) — deliberately **not** given block/unblock/retire/view_sensitive, matching the spec's "can view and assign, cannot manage lifecycle or see sensitive data" intent |
| `AUDITOR` | none added — `device.read` only, unchanged |
| `SUPER_ADMIN` | gets every permission automatically (`frozenset(p.name for p in ALL_PERMISSIONS)`) |

## 2. Tenant Isolation / IDOR

Devices are header-scoped (`X-Organization-ID`), not path-scoped, so the
IDOR story differs slightly from Phase 3's `/organizations/{id}/...`
resources:

- **Direct organization access** (a forged `X-Organization-ID` naming an
  org the caller isn't a member of): rejected at context-resolution time —
  `403 ORGANIZATION_ACCESS_DENIED` — before any device query runs. Same
  `resolve_organization_context` function Phase 3 built; Phase 4 reuses it
  unmodified via `get_current_organization`.
- **Cross-tenant device reference** (a device ID from another org, with a
  *valid* org header for the caller's own org): every device-repository
  method requires `organization_id` and filters on it
  (`DeviceRepository.get_for_organization`, `.update_for_organization`,
  etc. — no bare `get_by_id`). The device simply isn't found —
  `404 DEVICE_NOT_FOUND`, identical to the response for a genuinely
  nonexistent ID. The caller cannot distinguish "wrong tenant" from
  "doesn't exist."

Tested exhaustively in `tests/test_device_tenant_isolation.py`: GET,
PATCH, DELETE, assign, block, and retire against a cross-tenant device ID
all return 404; a forged `X-Organization-ID` header returns 403; the
device list is scoped to the caller's own organization; a `DISABLED`
organization loses device access entirely (`403 ORGANIZATION_SUSPENDED`,
reusing Phase 3's org-status check).

## 3. Untrusted `organization_id`

`CreateDeviceRequest` (and every other device request schema) has **no**
`organization_id` field at all — it is structurally impossible for the
client to supply one. The organization is always the caller's resolved
`OrganizationContext.organization_id`. Verified by
`test_create_device_ignores_client_supplied_organization_id`, which posts
an `organization_id` for a *different* organization in the JSON body and
confirms it's silently dropped by schema validation, with the created
device landing in the caller's actual organization.

## 4. Sensitive Identifiers (IMEI, Wi-Fi MAC)

**Storage.** Neither value is ever persisted in the clear. `POST /devices`
accepts optional `imei` / `wifi_mac` strings, normalizes them
(`app/devices/identity.py::normalize_identifier` — strips separators,
uppercases), and stores only:
- `*_hash`: SHA-256 hex digest of the normalized value, for
  organization-scoped deduplication (`uq_device_org_imei_hash`).
- `*_last4`: the last 4 characters of the normalized value, for a human to
  visually confirm which physical device a record refers to.

The raw value is never written to the database, never logged, and never
included in any API response — not even to the user who just submitted
it. There is deliberately **no reversible encryption**: Phase 4 has no
genuine, specified requirement to ever recover the full value, and adding
a decryption capability "just in case" would be exactly the kind of
speculative sensitive-data-at-rest liability the codebase's general
security posture (see `docs/architecture/security-architecture.md`)
argues against.

**Access control — an adapted design.** The spec's original framing
("`device.view_sensitive` gates access to the *full* value") doesn't
directly apply here, because Phase 4 never stores a full value for anyone
to reveal. Instead, `device.view_sensitive` gates the last-4 fragment
itself — the most granular sensitive data the system retains:

- `DeviceResponse.imei_last4` / `.wifi_mac_last4` are populated only when
  `OrganizationContext.has_permission("device.view_sensitive")` is true
  for the requesting caller (checked per-request in
  `app/devices/router.py::_device_to_response`, not cached). A caller
  without the permission sees `null` for both fields, on both the list and
  detail endpoints.
- This is a deliberate, documented interpretation choice, not an
  oversight — see `test_privileged_viewer_sees_last4` /
  `test_unprivileged_viewer_does_not_see_last4` in
  `tests/test_device_sensitive_data.py`.

**Audit logging.** Every time a caller who holds `device.view_sensitive`
fetches a single device's detail (`GET /devices/{id}`) and that device
actually has a non-null `imei_last4` or `wifi_mac_last4`, a
`DEVICE_SENSITIVE_DATA_VIEWED` audit event is recorded
(`actor_user_id`, `organization_id`, and the `device_id` — never the
fragment's actual value, to avoid putting sensitive data in a second
place). List endpoints do **not** trigger this event per row, to avoid
flooding the audit log on ordinary browsing; only the detail view does.
Verified in `test_sensitive_view_is_audited` /
`test_unprivileged_viewer_does_not_trigger_sensitive_audit`.

**Search.** `GET /devices?search=` matches `display_name`,
`serial_number`, `manufacturer`, `model`, `device_identifier`, and the
assigned user's email — never `imei_hash`, `imei_last4`, `wifi_mac_hash`,
or `wifi_mac_last4`. Searching on a hash would be useless (not
human-typable) and searching on the last-4 fragment would let an
unprivileged caller probe for it via timing/existence side channels
without ever seeing it directly, so it's excluded from the query
entirely, not just from the response.

## 5. Assignment Security

Covered in full in `docs/architecture/device-management.md` §6; summarized
here as a security control: assignment always independently re-verifies
(a) same-organization membership and (b) `ACTIVE` membership status
server-side, regardless of what the frontend's user picker shows. The
frontend's assignment dropdown (`app/(app)/devices/[id]/page.tsx`) filters
to `ACTIVE` members client-side purely for UX — it is not a security
boundary, and the backend enforces the same rule independently
(`test_cannot_assign_device_to_suspended_member`,
`test_cannot_assign_device_to_user_in_different_organization`).

## 6. No Fake Device Management

Per the Phase 4 spec's explicit rule: no endpoint in this phase issues, or
implies it issues, a real command to a physical device.
`POST /devices/{id}/block` changes `devices.status` to `BLOCKED` in the
database — nothing more. No Android Management API call, no APNs push, no
device-side effect of any kind. This is stated in the docstring of every
lifecycle endpoint in `app/devices/router.py` and reiterated here so it
isn't lost in a future refactor: when real Android/Apple integration
lands, the lifecycle endpoints will need an explicit follow-up design
decision about whether/how they trigger a real platform command — that
decision does not exist yet and must not be assumed.

## 7. Error Handling

Device errors follow the existing standardized envelope
(`app/core/errors.py`) — `{"error": {"code", "message", "request_id"}}`.
Device-specific codes introduced: `DEVICE_NOT_FOUND` (404),
`DEVICE_ALREADY_EXISTS` (409, duplicate `device_identifier` /
`serial_number` / IMEI within the organization),
`INVALID_LIFECYCLE_TRANSITION` (409), `DEVICE_RETIRED` (409, assignment
attempt), `USER_NOT_IN_ORGANIZATION` (404, assignment target),
`MEMBERSHIP_NOT_ACTIVE` (400, assignment target). No SQL error text, stack
trace, or ORM object representation is ever included in a response body —
unhandled exceptions fall through to the existing generic
`INTERNAL_SERVER_ERROR` handler.

## 8. Performance / Abuse Resistance

`GET /devices` filters, searches, sorts, and paginates entirely in the
database (`DeviceRepository.list_for_organization` builds one
`WHERE`/`ORDER BY`/`OFFSET`/`LIMIT` query — never loads a full table into
Python). `page_size` is capped at 100
(`test_pagination_rejects_excessive_page_size` — `page_size=1000000`
returns `422`, not a truncated-but-still-expensive result). Sort column is
resolved through an explicit whitelist
(`DeviceRepository._SORT_COLUMNS`) — the `sort` query parameter can never
be used to inject arbitrary SQL or reference a column outside that list.
