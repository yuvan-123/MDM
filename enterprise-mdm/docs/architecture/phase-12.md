# Phase 12 — Production Device Management Operations

Phases 6-11 built a genuinely complete command state machine, real
provider integration, and real enrollment identity — but three practical
gaps remained between "the architecture supports device management" and
"an admin can actually manage devices safely": an unsupported command
(e.g. REBOOT on an Apple device) was only discovered as a failure *after*
being queued for async dispatch; there was no way to ask ahead of time
which commands a given device could even accept; and there was no way to
remove a device from real platform management at all — only to revoke
this codebase's own enrollment record (Phase 11), which never touched the
provider side. Phase 12 closes these three, plus surfaces the
management/provider state Phase 9-11 already computed but never exposed
through the device API.

It does not rebuild the command state machine, the provider abstraction,
compliance evaluation, or background job infrastructure — every one of
those is reused exactly as Phase 6-11 left it.

**Verification status**: `python -c "import app.main"` succeeds, ruff and
`mypy --strict` are clean against every file this phase touches. See
`docs/security/phase-12-security-review.md` §4 for the exact commands and
current test-run status.

## 1. Capability gate moved earlier

Previously, `CommandService.create_command` always created a `PENDING`
row and enqueued async dispatch — capability checking
(`provider.supported_capabilities`) happened only inside `_dispatch`,
called later by the worker. A request for a command type the target
platform genuinely can't perform (Apple + REBOOT, Apple + POLICY_SYNC,
Apple + UNENROLL — see §3) would always end up `FAILED`, but only after a
full async round trip, with a command row and full audit trail for
something that could never have succeeded.

`create_command` now resolves the provider and checks
`_require_capability_supported` (a small pure function shared with
`_dispatch`, which still re-checks as defense in depth) **synchronously,
before creating any row** — this is cheap (no network I/O, just a
settings read and object construction) and rejects the request
immediately with 400 `COMMAND_TYPE_NOT_SUPPORTED`. Nothing about this
touches Phase 6's other deliberate design choice, preserved exactly:
when the provider itself is *unavailable* (real mode, missing
credentials — always true in this environment), that's a different,
non-deterministic failure category, and `create_command` still lets it
fall through to the existing PENDING-row-then-FAILED-on-dispatch path so
there's always an audited record that an attempt was made. See
`tests/test_device_management_phase12.py::test_provider_unavailable_still_creates_pending_row`
for the guarantee that this phase didn't regress that.

## 2. Capability discovery

`GET /devices/{id}/commands/capabilities` (new, `device.read`) —
`CommandService.get_supported_command_types` returns
`(provider_name, provider_available, supported_command_types)`. Lets a
caller (the frontend, §7) know which command buttons to even show as
executable, instead of discovering unsupported-ness only after a
rejected request. `provider_available=False` means the provider itself
couldn't be constructed at all — `supported_command_types` is empty in
that case, not a guess at what it might support if configured.

Registered *before* `GET /commands/{command_id}` in the router — FastAPI
matches path routes in registration order, so `/capabilities` would
otherwise be parsed as an attempted `command_id` UUID and 422.

## 3. UNENROLL — real Android removal, documented Apple gap

New `DeviceCommandType.UNENROLL` / `PlatformCapability.UNENROLL`,
`POST /devices/{id}/commands/unenroll` (new `device.command.unenroll`
permission, granted only to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN` — same
tier as `device.wipe`, not `HELP_DESK`). Same safeguard shape as wipe:
`confirm=true` and a required `idempotency_key`.

- **Android**: `AndroidManagementProvider.unenroll_device` calls new
  `AndroidApiClient.delete_device` — `enterprises.devices.delete`, the
  real API this codebase's own Phase 6 documentation already identified
  and *deliberately avoided* for WIPE ("a blunter 'delete the device
  record, best-effort wipe if reachable' operation... WIPE is the
  correct mapping for an explicit, in-place device action that keeps the
  device's inventory record intact"). UNENROLL is exactly the case that
  operation was always meant for. Unlike every other real Android
  command (an async `issueCommand` Operation, always `SENT`), this is a
  direct synchronous REST delete — the one Android command type that can
  honestly report `COMPLETED`.
- **Apple**: not added to `_SUPPORTED_CAPABILITIES`. Apple's real
  removal mechanism (a device processing `RemoveProfile`, or an
  admin/user removing it from Apple Business Manager) has no
  single push-and-confirm shape analogous to `devices.delete`, and
  wasn't part of this codebase's researched capability set
  (`docs/apple/mdm-capabilities.md`). `unenroll_device` still exists on
  `AppleManagementProvider` (the abstract method has a concrete
  signature now, so every subclass must implement it) but raises the
  same documented `PlatformCommandRejectedError` REBOOT/POLICY_SYNC
  already do — never reached in practice, since §1's capability gate
  rejects it first.
- **Mock**: supports it, deterministic, for testability.

**On COMPLETED** (never merely `SENT` — unlike WIPE's proactive
credential-clearing, deleting the `ProviderDeviceLink` entirely is a
stronger, less-reversible action worth waiting for genuine confirmation
on), `_dispatch` performs exactly what
`EnrollmentService.revoke_enrollment` already does for an
admin-initiated revocation (Phase 11 §5): clears the device's heartbeat
credential, marks `enrollment_status=REVOKED`, and deletes the now-stale
`ProviderDeviceLink` (audit-logged as `PROVIDER_DEVICE_LINK_DELETED`) so
a future physical device re-enrolling under the same provider identifier
never resolves back to this one. The `Device` row and its full history
(commands, audit trail) are preserved — never deleted.

## 4. Device management-state / provider visibility

`DeviceResponse` gains `management_state` (`"MANAGED"` if a
`ProviderDeviceLink` exists, `"UNMANAGED"` otherwise — computed, not
stored, matching this codebase's established pattern for
`connectivity_state`), `provider_name`, and `provider_device_id`.
Distinguishes "enrolled" (this codebase has a device identity and
check-in channel, Phase 5) from "managed" (a real provider identity
exists and remote commands can actually be dispatched, Phase 9-11) — a
distinction `Device`'s own module docstring has drawn in code comments
since Phase 5, now finally surfaced through the API.

Populated by the router (`app/devices/router.py::_with_provider_state`),
not by `DeviceResponse.from_device` itself (which has no DB access and
stays a pure ORM mapper, reused by `app/enrollment/router.py`'s
registration response too). `GET /devices` does one bulk lookup for the
whole page (`ProviderDeviceLinkRepository.get_links_for_devices`), never
N+1 per row. `POST /enrollment/register`'s response is kept consistent
with this too — when Phase 11's Path A creates a link in the same
request, the returned `DeviceResponse` now reflects `MANAGED`
immediately rather than looking `UNMANAGED` until the next `GET`.

`GET /devices?provider_name=android` (new filter) — an inner join in
`DeviceRepository._apply_filters`, correct because filtering *for* a
provider must never match a device with no link at all.

## 5. What's genuinely done vs. deferred

**Done:** capability gate + discovery, UNENROLL (Android real, Apple
documented boundary, Mock simulated), device management-state/provider
visibility + filter, all wired through the existing command/enrollment
architecture with no parallel systems introduced.

**Deliberately deferred, not attempted half-built:**
- **Compliance status on the device list** — `GET /compliance/devices/{id}`
  and `GET /compliance/summary` (Phase 5) already expose this; merging it
  onto every `GET /devices` row would need either a join or a second bulk
  lookup analogous to §4's provider one. Real value, but judged separate
  scope from this pass — not attempted shallowly.
- **Bulk device commands / group-targeted dispatch** — no existing
  primitive creates individual command records for a set of devices
  safely; building one is a genuine new capability, not an extension of
  something that already exists in shape. Per this phase's own
  instruction ("implement only if the current architecture supports safe
  bulk operations"), not built rather than built unsafely.
- **A reconciliation job for management-state drift** — evaluated: there
  is no drift scenario to reconcile. `management_state` is computed at
  read time directly from `ProviderDeviceLink`'s current existence, never
  cached or duplicated anywhere a sweep would need to correct.
