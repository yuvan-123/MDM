# Remote Device Command & Management (Phase 6)

Builds directly on `docs/architecture/device-management.md` (Phase 4
inventory) and `docs/architecture/enrollment-and-policy.md` (Phase 5
enrollment/credential model). This document covers what Phase 6 adds: a
generic command system that can actually reach a physical device, through
a platform abstraction the core MDM domain never sees past.

## 1. Scope and Non-Goals

Phase 5's enrollment gave a device a verified backend identity and a
one-way check-in channel (heartbeat) — it could not yet be *acted on*.
Phase 6 makes that real for the first time: `POST
/devices/{id}/commands/lock` (and reboot/wipe/refresh/policy-sync)
actually attempt to reach the device through a
`PlatformManagementProvider`. But "actually attempt" is doing real work in
that sentence — see §7 for exactly what is, and is not, verified to
happen on a real device in this deployment.

The architectural rule holds absolutely: the generic MDM layer
(`app/commands/`) knows five verbs — `LOCK`, `REBOOT`, `WIPE`,
`REFRESH_INFO`, `POLICY_SYNC` — and nothing about how any platform
actually performs them. `CommandService` depends on
`PlatformManagementProvider`, never on `AndroidManagementProvider` or
`AppleManagementProvider` directly.

## 2. Platform Abstraction

```
CommandService
      │
      ↓
PlatformProviderRegistry.get_provider(device.platform)
      │
      ├── MockManagementProvider      (default — platform_provider_mode="mock")
      ├── AndroidManagementProvider   (platform_provider_mode="real", ANDROID)
      └── AppleManagementProvider     (platform_provider_mode="real", IOS/IPADOS)
```

`app/platform/base/provider.py::PlatformManagementProvider` is the
abstract boundary (reserved since Phase 1, given typed method signatures
in Phase 6). Every provider declares `supported_capabilities: frozenset[PlatformCapability]`
(`app/platform/base/capabilities.py`: `LOCK`, `REBOOT`, `WIPE`,
`REFRESH_INFO`, `POLICY_SYNC`) — `CommandService` checks this *before* any
dispatch attempt, so an unsupported combination for a given platform is
rejected cleanly rather than attempted and failing inside the provider.
Capability support is **not assumed uniform across platforms** — see
`docs/apple/mdm-capabilities.md` for a concrete case (`REBOOT` genuinely
does not exist as an iOS/iPadOS MDM command).

Selection is controlled by `Settings.platform_provider_mode` (`"mock"` |
`"real"`), defaulting to `"mock"` — per the pre-existing mock-adapter
policy in `docs/architecture/android-enterprise.md` §5, this is never a
silent fallback for missing credentials; a real provider that lacks
required configuration raises `PlatformProviderUnavailableError`
(`app/platform/base/errors.py`) at construction, and `CommandService`
records that as a `FAILED` command with a full audit trail rather than an
unhandled exception (§4).

## 3. Command Model

`app/commands/models.py::DeviceCommand` — one row per remote action,
`organization_id`/`device_id` FK-indexed like every other Phase 4/5 domain
table. `DeviceCommandType`: `LOCK`, `REBOOT`, `WIPE`, `REFRESH_INFO`,
`POLICY_SYNC`. `DeviceCommandStatus` state machine:

```
PENDING --dispatch succeeds--> SENT --provider reports COMPLETED--> COMPLETED
PENDING --dispatch itself errors (provider unavailable, unsupported)--> FAILED
SENT --provider reports FAILED--> FAILED
SENT --admin cancels--> CANCELLED
SENT --now >= expires_at (lazy, evaluated at read time)--> EXPIRED
COMPLETED / FAILED / CANCELLED / EXPIRED: terminal
```

`PENDING` is a real, briefly-observed row state — dispatch happens
synchronously within the same request that creates the row (§4), so no
caller ever meaningfully sees `PENDING` outside a database snapshot mid-
request. There is no `ACKNOWLEDGED` state: nothing in this codebase could
ever produce that transition (see §7), so it was deliberately not
invented just to make the state machine feel more complete than the real
capabilities support.

## 4. Dispatch Model — Why SENT, Not COMPLETED, For Real Providers

Both Android Management API's `issueCommand` and Apple's MDM push+check-in
are **genuinely asynchronous**: the platform accepts the command and
reports back later, through a mechanism this codebase does not implement
(§7). There is no worker/queue infrastructure here to defer dispatch to,
so `CommandService.create_command` dispatches synchronously within the API
request, and each provider reports only what it can truthfully know at
that moment:

- **MockManagementProvider**: every action resolves deterministically to
  `COMPLETED` (or `FAILED` via a test-only `simulate_failure` payload key)
  — honest because every response is explicitly labeled "Simulated" and
  selectable only via explicit configuration, never a silent fallback.
- **AndroidManagementProvider**: every command (including `REFRESH_INFO`,
  deliberately mapped to the async `REQUEST_DEVICE_INFO` rather than a
  synchronous cache read — see `docs/android/enterprise-capabilities.md`)
  reports `SENT` only.
- **AppleManagementProvider**: everything reports `SENT` only — even a
  "read" is a push + check-in round trip.

Provider resolution itself is wrapped in the same try/except as the
dispatch call (`CommandService._dispatch`) — a `PlatformProviderUnavailableError`
raised by a real provider's constructor, or a `COMMAND_TYPE_NOT_SUPPORTED`
capability mismatch, both still produce a persisted, audited `FAILED`
command row. The alternative (rejecting before a row exists) would mean an
admin who attempts to command a misconfigured real-provider device gets an
error with **no record an attempt was ever made** — worse for the exact
auditability this whole system exists to provide.

## 5. Idempotency

`UniqueConstraint(organization_id, device_id, idempotency_key)` on
`device_commands`, NULLs distinct (matches `Device`'s own unique
constraints) — commands without a key never collide with each other.
`create_command` does a SELECT-before-INSERT check, then catches the
`IntegrityError` fallback for the (accepted, documented) race window where
two requests race between that SELECT and the INSERT — the loser recovers
the session (`DeviceCommandRepository.rollback()`) and returns the row the
winner just created, never dispatching twice. This is a **known, accepted
race window**, not a false guarantee: SQLite's single-writer nature makes
it hard to even construct a test that reliably exercises the raw race
(see `tests/test_commands.py::test_repeated_idempotency_key_returns_same_command`
for the sequential case, which the SELECT-before-INSERT check alone
already handles).

**Wipe requires an idempotency key** — every other command type leaves it
optional — so a client retry can never double-dispatch a destructive
action (§6).

## 6. Wipe Safeguards

1. RBAC: `device.wipe` — ORGANIZATION_ADMIN and SECURITY_ADMIN only
   (pre-existing permission, unchanged from Phase 4's reservation of it).
2. `IssueWipeCommandRequest.confirm: Literal[True]` — a required field, not
   optional; an empty or minimal POST body can never trigger a wipe.
3. `idempotency_key` is **required** on the wipe request specifically (§5).
4. A wipe reaching `SENT` immediately clears `Device.device_secret_hash`
   — the device's heartbeat credential is treated as revoked the moment a
   wipe is dispatched, not deferred to a completion signal this codebase
   may never receive from a real provider (`AndroidManagementProvider`
   and `AppleManagementProvider` never report `COMPLETED` for a wipe —
   see §4). This mirrors exactly how Phase 5's `revoke_enrollment` clears
   the same field.
5. Standard audit trail (`DEVICE_COMMAND_CREATED`/`_DISPATCHED`/`_FAILED`),
   proportional to how `block`/`retire` are already audited — no new
   `SecurityEvent` type, matching that precedent.

## 7. Known Gap — No Completion-Callback Mechanism

The single biggest honest limitation of this phase: **a real-provider
command can sit in `SENT` indefinitely**, with no automatic path to
`COMPLETED`. Closing this loop would require:

- **Android**: a Cloud Pub/Sub subscriber consuming Android Management
  API's device-state-change notifications and polling `operations.get`
  for the `issueCommand` `Operation`'s eventual result.
- **Apple**: a new authenticated HTTPS endpoint (the actual MDM
  command-queue "channel 2" — see `docs/apple/mdm-capabilities.md`) that
  a real device would call, plus plist request/response handling.

Neither existed as of this phase. **Update, Phase 9**: both are now built
— `app/platform/android/router.py` (Pub/Sub push webhook) and
`app/platform/apple/checkin.py`/`router.py` (the real channel-2 command
queue), bridging into `CommandService.apply_provider_event` (Phase 8). See
`docs/architecture/phase-9.md` for the full design and its honest limits:
neither is connectable to a real device without real platform enrollment
(Android Enterprise / Apple ADE) that no phase, including Phase 9,
implements — `SENT` commands in *this* environment still only leave that
state via admin cancellation or lazy/proactive expiration
(`device_command_expire_hours`, reconciled by `app/workers/loop.py` since
Phase 7), because nothing here has ever received a real completion signal
from an actual device.

## 8. API Surface

See `app/commands/router.py`'s module docstring for the authoritative
endpoint list (issue routes, list, get, capabilities, cancel — all nested
under `/devices/{device_id}/commands`, admin-authenticated via
cookie+CSRF like every other Phase 4/5 mutating endpoint, no new CSRF
exemption needed).

**Phase 12 added** a sixth command type, `UNENROLL` (real removal from
Android management via `enterprises.devices.delete` — the operation §5's
WIPE mapping deliberately avoided, finally used for the case it's correct
for; no supported Apple mapping, documented not guessed), a
`GET .../commands/capabilities` discovery endpoint, and moved the
capability check (`provider.supported_capabilities`) to run
synchronously in `create_command` for the deterministic
"platform doesn't support this" case — the "provider unavailable"
failure mode this section already documents is unchanged, still an
audited PENDING-then-FAILED row. See `docs/architecture/phase-12.md`.

## 9. Security

See `docs/security/phase-6-security-review.md` for the full threat table.
