# Provider Event Ingestion & Processing (Phase 7 ingestion, Phase 8 processing/resolution)

Builds on `docs/architecture/device-commands.md` (the
`PlatformManagementProvider` abstraction this is the inbound counterpart
to), `docs/architecture/background-jobs.md` (the command-dispatch flow
`COMMAND_COMPLETED`/`COMMAND_FAILED` events bridge back into), and
`docs/android/enterprise-capabilities.md` / `docs/apple/mdm-capabilities.md`.

## 1. Scope and non-goal

Real Android Enterprise deployments receive device/policy status changes
via Google Cloud Pub/Sub; real Apple MDM deployments receive them via a
device's periodic HTTPS check-in. **Neither exists in this codebase.**
There is no Pub/Sub subscriber and no MDM check-in endpoint — Phase 6
already documented this as the reason a dispatched command can sit in
`SENT` indefinitely (`docs/architecture/device-commands.md` §7).

Phase 8 still does not build either of those entry points — that remains
future work, since building a webhook endpoint with nothing real to call
it would just be more unverified surface. What Phase 8 *does* build is
everything behind where that endpoint would eventually call in:
`app/provider_events/service.py::ingest_event` now actually processes
what it ingests (§7) and resolves devices through an explicit,
tenant-safe mapping (§3) instead of Phase 7's `Device.device_identifier`
guess. `ingest_event` remains a **callable service method only** — no
router — since there is still no genuine HTTP caller for one to serve.

## 2. Idempotency

`ProviderEvent` (`app/provider_events/models.py`) has a unique constraint
on `(provider_name, provider_event_id)` — both real providers'
delivery models are at-least-once, so redelivery of the same event must be
a no-op: no duplicate row, no re-resolution, and (Phase 8) no re-running
of whatever handler already processed it once. `ingest_event` checks for
an existing row by that pair first and returns it unchanged on a match,
*before* device resolution or processing ever runs.

## 3. Device resolution: `ProviderDeviceLink` (Phase 8)

`ingest_event` accepts an optional `organization_id` and
`provider_device_id`. Resolution to an internal `Device` is attempted
**only when both are supplied** — a `provider_device_id` is only unique
*per (organization, provider)*; resolving without an organization context
would be a genuine cross-tenant ambiguity, not just an edge case worth a
comment. If `organization_id` is omitted, the event is still recorded
(with `device_id`/`organization_id` left `NULL`) rather than rejected — an
event whose organization can't yet be determined is still worth keeping.

Resolution itself goes through `ProviderDeviceLink`
(`app/provider_events/models.py`) — an explicit `(organization_id,
provider_name, provider_device_id) -> device_id` mapping, replacing
Phase 7's approach of guessing via `Device.device_identifier`. That guess
conflated two distinct concepts: the enrollment-time identifier Phase 5
captures, and a provider's own device identifier — related, but not the
same thing, and treating them as interchangeable was honest-but-imprecise.
The new mapping's unique constraint (`uq_provider_device_link`) makes
"ambiguous resolution" structurally impossible, not merely avoided by
caller discipline: `ProviderDeviceLinkRepository.get_device_id` can never
return more than one candidate, so there is no "pick the first match"
logic anywhere to get wrong.

**Nothing populates this table yet.** No real Android/Apple enrollment
exists in this codebase (§4), so there is no genuine
`provider_device_id` to link against in practice — resolution is correct
in shape, exercised only by tests that insert a link row directly
(`tests/test_provider_events.py`).

## 4. A real gap this inherits from Phase 6

`ProviderDeviceRef.provider_device_id` (the outbound direction, used when
*dispatching* a command) is never populated anywhere, because Phase 5's
enrollment is a synthetic backend-only identity, not genuine Android
Enterprise or Apple MDM enrollment (`docs/architecture/device-commands.md`
§7). `ProviderDeviceLink` (§3) is the inbound-direction mapping that would
receive a provider's device identifier if one ever arrived — but nothing
in this codebase's enrollment flow produces one to link against yet.
Until a later phase performs real platform enrollment, both directions of
provider-device identity remain correct in shape, unexercised in
substance.

## 5. Not audited

Ingestion does not write an `AuditLog` entry, matching the existing
precedent for `DeviceService.record_heartbeat` (Phase 5): both are
high-volume, system/device-originated, non-admin-action data points where
per-event audit logging would flood the audit trail rather than usefully
inform it. `received_at`/`processed_at` on the row itself, plus the
`processing_status`/`processing_error` columns (§7), are the durable
record.

## 6. Admin API (Phase 8) and why unresolved events are invisible to it

`GET /provider-events`, `GET /provider-events/{id}` — flat,
`X-Organization-ID`-scoped, gated by `provider_event.read` (same role
grants as `job.read` — `ORGANIZATION_ADMIN`/`SECURITY_ADMIN`/`AUDITOR`).
Both queries filter on `organization_id` matching the caller's own
resolved tenant — an event with `organization_id=NULL` (unresolved, §3)
is invisible through this API for *every* organization, not shown to
whichever one asks first. There is no platform-wide "show me every
orphaned event" surface in this codebase (no precedent for one exists —
even `AUDIT_READ`, reserved since Phase 2, has no router yet), so this is
an accepted Phase 8 boundary: an operator who needs to inspect an
unresolved event does so by reading the database directly, not through
this API. `payload` is returned as-is, not re-sanitized at this layer —
the same "must never contain credentials" invariant this codebase already
relies on for `DeviceCommand.result`/`payload` is enforced at ingestion
time, not re-checked on every read.

**Phase 10 added `POST /provider-events/{id}/retry`** (gated by a
separate `provider_event.retry` permission, not granted to `AUDITOR`) —
only a `FAILED` event is retryable, and retry re-runs `_process_event`
directly rather than `ingest_event`, so it never interacts with the
`(provider_name, provider_event_id)` redelivery-idempotency guarantee
described in §2. See `docs/architecture/phase-10.md` §6.

## 7. Event processing (Phase 8)

`app/provider_events/handlers.py` is a registry
(`event_type string -> handler function`), not an if/elif chain — adding
a new event type means writing a function and registering it, mirroring
`app/compliance/rules.py`'s `RULES`-list extension pattern. `ingest_event`
calls the registered handler (if any) immediately after creating the row,
inside the same call — there is still no genuine caller with request-
latency pressure to defer this off to a job for, unlike command dispatch
(`docs/architecture/background-jobs.md` §7).

An event type with **no registered handler** is recorded
`processing_status=IGNORED`, not `FAILED` — not yet acting on an event
type isn't an error; a *recognized* type whose handler raises is `FAILED`,
with `processing_error` capturing why. Five event types are registered
today, none of them producible by any real provider yet (§1):

- **`COMMAND_COMPLETED`** / **`COMMAND_FAILED`** — bridge to
  `CommandService.apply_provider_event` (§8). Payload must include a
  string `provider_command_id`.
- **`COMMAND_ACKNOWLEDGED`** — recognized (so it's `PROCESSED`, not
  `IGNORED`) but makes **no** `DeviceCommand` state change: Phase 6
  deliberately has no `ACKNOWLEDGED` status ("sent but not yet known to
  have executed" already IS `SENT`), and inventing one here just to have
  somewhere to put this event would violate the
  state-machine-preservation rule reaffirmed in Phase 8's brief.
- **`DEVICE_CHECK_IN`** — requires a resolved `device_id`; schedules a
  `COMPLIANCE_EVALUATION` job, exactly like the existing heartbeat
  trigger (`docs/architecture/background-jobs.md` §5) — a provider-
  originated check-in is treated identically to a device's own heartbeat.
- **`DEVICE_UPDATED`** — requires a resolved `device_id`; syncs only
  `os_version`/`client_version` from the payload, mirroring
  `CommandService._dispatch`'s `REFRESH_INFO` device-field sync exactly
  (same two fields, same "only known fields, never an arbitrary payload
  key" rule).

Every handler that needs an organization (all five) raises immediately if
`event.organization_id is None` — fail safely, per §3's resolution rule,
never guess.

## 8. Provider event -> command completion bridge

`CommandService.apply_provider_event` (see
`docs/architecture/background-jobs.md` §7 for the dispatch side this
completes) is the single place "never mark a command COMPLETED merely
because a provider event exists" is enforced:

- resolves the target command by `provider_command_id` **scoped to the
  event's own `organization_id`** — never a bare, unscoped lookup;
- accepts a transition only from `SENT` to a terminal status
  (`COMPLETED`/`FAILED`) — a `PENDING` command (dispatch not yet
  attempted) or an already-terminal one can never be "advanced" by an
  external event, whether out-of-order, replayed, or forged;
- is the *only* place either `_handle_command_completed` or
  `_handle_command_failed` writes to `DeviceCommand` — neither handler
  touches the repository directly.

Combined with §2's idempotency (a redelivered `COMMAND_COMPLETED` event
returns the original row without re-processing), a command can be
completed by an inbound event at most once, and only from the one state
that legitimately precedes completion.

## 9. Known gaps / deferred

- No router / real webhook entry point (Android Pub/Sub subscriber, Apple
  MDM check-in HTTPS endpoint) — still nothing genuine to wire it to.
- `ProviderDeviceLink` has no real provider-native identifier to resolve
  against yet (§3/§4) — nothing in this codebase's enrollment flow
  produces one.
- No platform-wide surface for an unresolved (organization_id=NULL) event
  (§6).
- `event_type` remains a free-form string, not validated against a real
  provider payload shape — there still isn't one to validate against.
