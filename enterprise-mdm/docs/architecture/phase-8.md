# Phase 8 — Operationalizing the Phase 7 Async Infrastructure

Phase 7 built the foundation: a Postgres-backed job queue, a worker loop,
command reconciliation, heartbeat/policy-triggered compliance scheduling,
and a provider-event table with idempotent ingestion. None of it was
reachable by an admin, none of it processed an event by type, remote
commands still dispatched synchronously inline, and the worker was
correct for exactly one process. Phase 8 closes those five specific gaps
without re-deciding anything Phase 7 already settled — no Celery, no new
worker architecture, no new job-table shape.

## 1. What changed, in one pass

- **Admin visibility**: `GET /jobs`, `GET /jobs/{id}`, `GET
  /provider-events`, `GET /provider-events/{id}` — read-only, org-scoped,
  gated by two new permissions (`job.read`, `provider_event.read`). See §2
  and `docs/architecture/background-jobs.md` §9 /
  `docs/architecture/provider-events.md` §6.
- **Provider-event processing**: a handler registry
  (`app/provider_events/handlers.py`) replaces "ingest and do nothing" —
  `COMMAND_COMPLETED`/`COMMAND_FAILED` bridge to `DeviceCommand` state,
  `DEVICE_CHECK_IN` schedules compliance evaluation, `DEVICE_UPDATED`
  syncs known device fields, `COMMAND_ACKNOWLEDGED` is recognized but
  makes no state change (Phase 6 has no matching status — see
  `docs/architecture/provider-events.md` §7).
- **Tenant-safe provider-device resolution**: `ProviderDeviceLink`
  (organization + provider + provider_device_id -> device) replaces
  Phase 7's `Device.device_identifier` guess. See
  `docs/architecture/provider-events.md` §3.
- **Command dispatch moved off the request path**: `create_command` now
  enqueues a `COMMAND_DISPATCH` job instead of calling the provider
  inline. This is a genuine behavior change — see §3 below, it's the
  riskiest thing in this phase.
- **Multi-worker-safe claiming**: `claim_next` uses `SELECT ... FOR
  UPDATE SKIP LOCKED` on Postgres, plus lease-timeout recovery for a
  crashed worker's stuck `CLAIMED` job. See
  `docs/architecture/background-jobs.md` §8/§10.

## 2. Admin APIs — design choices

Both new resources are flat, header-scoped (`X-Organization-ID`)
resources using the exact same `require_current_organization_permission`
pattern as devices/policies/compliance — no new authorization mechanism.
Two consequences worth stating explicitly:

- **`background_jobs.organization_id` is `NOT NULL`** for every row this
  codebase ever creates (there is no system-wide job type — reconciliation
  stayed a plain sweep in Phase 7, not a job row). So "system-level jobs
  vs. organization-scoped jobs" isn't a distinction the admin API has to
  make: every job IS organization-scoped, full stop. `claim_next` (the
  worker's own query) is the one place that legitimately ignores
  `organization_id`, and it's not part of the admin surface.
- **`provider_events.organization_id` IS nullable** (an event with no
  resolvable tenant). The admin API's `get_for_organization`/
  `list_for_organization` both filter on it — an unresolved event is
  invisible through this API for every organization, not shown to
  whichever org asks first. There's no platform-wide "show me orphaned
  events" surface in this phase; see
  `docs/architecture/provider-events.md` §6 for why that's an accepted
  boundary rather than a gap nobody noticed.

Neither RBAC grant reaches `HELP_DESK` — jobs/events are operational
infrastructure, not part of that role's device-facing scope
(`app/rbac/permissions.py`).

## 3. Command dispatch: the compatibility-breaking change

Before Phase 8, `CommandService.create_command` called `_dispatch`
inline — the HTTP response already carried the provider's outcome
(`COMPLETED`/`SENT`/`FAILED`). Phase 8's instruction is explicit: don't
block the request on the provider. So `create_command` now enqueues a
`COMMAND_DISPATCH` job (`payload: {"device_command_id": ...}`,
`dedup_key` = the command's own id) and returns the command **PENDING**.
`app/workers/loop.py` claims that job and calls
`CommandService.process_dispatch_job`, which reuses `_dispatch`
unchanged — every existing safeguard (capability check, WIPE credential
revocation, REFRESH_INFO device sync, audit logging) fires exactly as
before, just later.

This broke roughly fifteen Phase 6 tests that asserted the POST response
itself carried `COMPLETED`/`SENT`. They were updated, not deleted —
`tests/conftest.py::dispatch_pending_command` drives one command's
dispatch synchronously in a test (mirroring exactly what the worker does
for one claimed job), so tests assert on the real dispatch outcome
instead of a response body that no longer carries it. See
`tests/test_commands.py`, `tests/test_command_state_machine.py`,
`tests/test_command_wipe_safeguards.py`,
`tests/test_command_platform_provider_unavailable.py` for the pattern.

### Dispatch idempotency (why WIPE is still safe)

Three independent guards, not one:

1. **Job-level dedup**: at most one active `COMMAND_DISPATCH` job can
   exist per command (`dedup_key`).
2. **Status guard**: `process_dispatch_job` checks the command is still
   `PENDING` before calling `_dispatch` — a job retried after its first
   attempt already succeeded and committed is a no-op.
3. **Provider-event bridge is SENT-only**: `apply_provider_event` (§4)
   can never re-trigger a dispatch — it only ever transitions an already-
   `SENT` command to a terminal state, never re-invokes the provider.

**The one gap none of these close**: a worker crash strictly *between* a
successful provider call and the commit that would have recorded it. Our
own row still shows `PENDING`; the provider may have already executed
the action. This is the same category of gap Phase 6 already documented
for cancellation ("can never actually recall a push") — solving it for
real needs provider-side idempotent request IDs, which this codebase has
no verified way to supply to a provider that has never been used live.
Documented, not silently ignored, and not fabricated a fix for (Phase 8
§41: no fake provider integration).

## 4. Provider-event -> command bridge

`CommandService.apply_provider_event` is the single control point for
"never mark a command COMPLETED merely because a provider event exists"
(§27/§28 of the brief). It:

- resolves the command by `provider_command_id` **within the event's own
  `organization_id`** (never blind) — `DeviceCommandRepository.get_by_provider_command_id`
  is org-scoped like every other lookup in that repository;
- only accepts a transition from `SENT` to a terminal status — a
  `PENDING` command (never dispatched) or an already-terminal one can
  never be "advanced" by an external event, out-of-order or replayed;
- is the only thing `app/provider_events/handlers.py`'s
  `_handle_command_completed`/`_handle_command_failed` call — no handler
  ever writes to `DeviceCommand` directly.

An event with no `organization_id` (unresolved) cannot reach this bridge
at all — the handler raises before even trying, and the event is
recorded `FAILED` with a clear reason, not silently dropped or guessed.

## 5. What's still real work for a later phase

- No live Postgres anywhere in this development environment — every
  concurrency/locking claim in §8 of background-jobs.md is verified by
  code review of the SQL construct used, not by an actual two-worker
  test.
- No real Android/Apple traffic exists — every event-type handler is
  exercised only by directly-constructed test events, never a real
  webhook.
- Editing an already-`ACTIVE` policy's configuration still doesn't
  re-trigger compliance evaluation for existing assignees (a Phase 7 gap,
  unchanged).
- No manual job-retry admin action (Phase 7's automatic
  retry-with-backoff already covers transient failures); see
  `docs/architecture/background-jobs.md` §9.
