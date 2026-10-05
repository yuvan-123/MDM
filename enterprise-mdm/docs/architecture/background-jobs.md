# Background Jobs, Reconciliation & Compliance Scheduling (Phase 7, extended Phase 8)

Builds on `docs/architecture/transaction-management.md` (the transaction
fix that makes any of this durable in the first place),
`docs/architecture/device-commands.md` §7 (the completion-callback gap this
phase's reconciliation sweep addresses), and
`docs/architecture/enrollment-and-policy.md` (policy precedence, which
`POLICY_REEVALUATION_FANOUT` re-triggers). See `docs/architecture/phase-8.md`
for the full list of what Phase 8 added on top of this foundation.

## 1. Why not Celery

`pyproject.toml` declares a Celery dependency that is **100% unused** —
`app/core/redis.py`'s own docstring defers it to "Phase 25", and the
architecture diagram in `docs/architecture/architecture.md` shows it as a
never-wired placeholder. Standing up a real Celery worker + broker for this
phase's actual needs (a handful of lightweight, infrequent job types) would
be substantial new infrastructure for a codebase where Redis itself has
never been exercised beyond a readiness check.

Instead: a Postgres-backed `background_jobs` table
(`app/jobs/models.py::BackgroundJob`) plus a simple polling worker loop
(`app/workers/loop.py`), mirroring `DeviceCommand`'s (Phase 6) conventions
exactly — one auditable row per unit of work, moved forward through an
explicit status state machine.

## 2. Job model

```
PENDING --claimed by a worker cycle--> CLAIMED --succeeds--> COMPLETED
CLAIMED --fails, transient, attempts remaining--> PENDING (rescheduled, backed off)
CLAIMED --fails, permanent OR attempts exhausted--> FAILED
```

Four job types exist today (`app/jobs/models.py::JobType`):

- **`COMPLIANCE_EVALUATION`** — evaluate one device's compliance
  (`payload: {"device_id": ...}`). Enqueued directly by a device heartbeat,
  or as the per-device step of a `POLICY_REEVALUATION_FANOUT` page.
- **`POLICY_REEVALUATION_FANOUT`** — pages through every device covered by
  a `DEVICE_GROUP`/`USER`/`ORGANIZATION`-scoped policy assignment change.
- **`COMMAND_DISPATCH`** (Phase 8) — dispatches one already-created
  `DeviceCommand` (`payload: {"device_command_id": ...}`). See §7.
- **`COMPLIANCE_REMEDIATION`** (Phase 13) — creates the `DeviceCommand`
  that remediates one `PolicyViolation`
  (`payload: {"violation_id": ..., "command_type": ...}`), enqueued by
  `ComplianceService._reconcile_violations` (itself running inside
  `COMPLIANCE_EVALUATION`'s own processing) when a policy's
  `remediation.mode` is `COMMAND`. `dedup_key` is the violation's own
  id — one active remediation attempt in flight per violation at a
  time. See `docs/architecture/phase-13.md`.

`dedup_key` collapses repeated triggers for the same target into one
queued row: `JobService._enqueue` checks
`BackgroundJobRepository.get_active_by_dedup_key` (PENDING/CLAIMED only)
before inserting. A burst of heartbeats from one device, or two admins
racing to assign the same policy, both produce exactly one active job —
see `tests/test_jobs.py`'s dedup tests and
`tests/test_heartbeat.py::test_heartbeat_enqueues_one_compliance_job_despite_repeated_checkins`.
A fanout chain's own re-enqueued pages deliberately carry `dedup_key=None`
— dedup exists to collapse repeated *triggers*, not the pagination chain a
single trigger produces.

## 3. The worker loop

`app/workers/loop.py::run_worker_once(session_factory)`:

1. Runs `CommandService.reconcile_expired_sent_commands()` (§4) and
   commits.
2. Claims at most one eligible job via `BackgroundJobRepository.claim_next()`
   (§8 for the Phase 8 concurrency-safety details).
3. Processes the claimed job (`_process_job`) and marks it COMPLETED, or
   catches any exception, classifies it via `app/jobs/retry.py::is_transient`,
   and marks it FAILED (permanent) or PENDING again with exponential
   backoff (`min(2**attempts, 300)` seconds) via `mark_failed`.

`session_factory` is a parameter — not the module-level `AsyncSessionLocal`
— specifically so tests can pass `TestingSessionLocal` directly, fully
testable without Postgres/Docker (see `tests/test_worker.py`).
`scripts/worker.py` is the real CLI entrypoint (`python scripts/worker.py`),
mirroring `scripts/seed_admin.py`'s sys.path bootstrap pattern.

### Retry classification

`app/jobs/retry.py::is_transient` is a standalone classifier keyed by
`AppError.code`, not a new field on `PlatformProviderUnavailableError`/
`PlatformCommandRejectedError` (`app/platform/base/errors.py`) — neither is
actually raised by any Phase 7 job-processing path yet, so touching Phase
6's verified error classes for a capability nothing exercises today was
judged unjustified risk. A recognized `AppError` not in the transient set
(e.g. `DEVICE_NOT_FOUND` — the device was deleted, retrying can never help)
is permanent. Any other exception (a DB hiccup, an unanticipated bug) is
treated as transient: the safer default is to retry rather than silently
drop a job that failed for a reason nothing here anticipated.

## 4. Command reconciliation — a plain sweep, not a job row

`CommandService.reconcile_expired_sent_commands` transitions every
`SENT` `DeviceCommand` past its `expires_at`, across every organization, to
`EXPIRED` — the proactive counterpart to the lazy, read-time expiration
`_effective_command_status` already performed (Phase 6). It is called
directly by the worker loop every cycle, **not** modeled as a
`background_jobs` row: a sweep over every organization has no natural
single subject or dedup key, and is trivially self-healing (a command
missed one cycle is still exactly as expired the next), so forcing it into
the one-job-one-outcome `BackgroundJob` shape was judged the wrong fit.

This directly addresses the largest deliberate gap called out in
`docs/architecture/device-commands.md` §7: with no Android Pub/Sub
subscriber and no Apple MDM check-in HTTPS endpoint, a real-provider
command could previously sit in `SENT` forever with nothing to move it
forward except an admin reading it (triggering lazy expiry) or cancelling
it by hand. The worker loop now does that proactively.

`DeviceCommandRepository.list_expired_sent_unscoped` is the one legitimate
cross-tenant query on that repository — a system-level sweep, not an
admin-facing lookup — mirroring the existing precedent of
`DeviceRepository.get_by_id_unscoped`.

## 5. Compliance scheduling

Two triggers, both landing on the same `COMPLIANCE_EVALUATION` job type:

**Heartbeat-triggered**: `POST /devices/{id}/heartbeat`
(`app/devices/router.py::device_heartbeat`) enqueues a compliance
evaluation for the checking-in device after recording the heartbeat.
Heartbeats are high-frequency and must stay fast, so evaluation runs
off-request via the worker queue, not inline — matching the existing
"heartbeats are not audited" precedent in `DeviceService.record_heartbeat`
(too frequent to handle synchronously without adding request latency).

**Policy-change-triggered**: `PolicyService.assign_policy` and
`unassign_policy` (`app/policies/service.py`) both call
`JobService.enqueue_policy_reevaluation` after the assignment change is
persisted and audited. Unassignment re-evaluates too — devices previously
covered by the removed assignment may now fall back to a lower-precedence
policy or to `UNKNOWN`. Editing an *already-assigned* policy's
configuration (rather than changing which scopes it's assigned to) does
**not** currently re-trigger evaluation — an accepted Phase 7 gap; those
devices pick up the new configuration at their next heartbeat.

`JobService.enqueue_policy_reevaluation` branches on scope type
(`app/policies/models.py::PolicyScopeType`):

- **`DEVICE`** — resolves to exactly one device, so it schedules a
  `COMPLIANCE_EVALUATION` job directly. No fanout needed.
- **`DEVICE_GROUP` / `USER` / `ORGANIZATION`** — schedules the first page
  of a `POLICY_REEVALUATION_FANOUT` chain
  (`payload: {scope_type, scope_id, cursor_offset: 0, page_count: 0}`).

### The fanout

`app/workers/loop.py::_process_policy_reevaluation_fanout` pages through
the scope's devices 200 at a time (`_FANOUT_PAGE_SIZE`) using the existing
paginated repository methods (`DeviceGroupRepository.list_devices_for_group`,
`DeviceRepository.list_for_organization` for `USER`/`ORGANIZATION`),
evaluates compliance for each device in the page directly (not by
enqueueing one `COMPLIANCE_EVALUATION` job per device — that would flood
the queue for a large organization), and re-enqueues itself with
`cursor_offset` advanced by the page size if the page was full — capped at
`_FANOUT_MAX_PAGES` (500, ~100k devices). A scope larger than that needs a
follow-up trigger; an accepted Phase 7 limitation.

This was deliberately **not** built as bulk device enumeration — no "all
devices affected by policy X" method exists anywhere in this codebase, and
loading every device for an org-wide policy change into memory at once was
judged over-engineering for a codebase explicitly designed around
10k-1M-device organizations (`app/repositories/device_repository.py`'s own
module docstring). See `tests/test_worker.py::test_policy_reevaluation_fanout_pages_through_every_device`
for the pagination/re-enqueue behavior proven against a monkeypatched small
page size.

## 6. Known gaps / deferred

- Editing an already-ACTIVE policy's configuration doesn't re-trigger
  evaluation of its existing assignees (§5).
- `migrations/env.py` was independently missing `import app.commands.models`
  since Phase 6 (meaning `device_commands` was invisible to
  `Base.metadata`/autogenerate) — fixed in Phase 7 alongside that phase's
  own new model imports.

## 7. Command dispatch (Phase 8)

`CommandService.create_command` used to call `_dispatch` inline — the
issuing HTTP request blocked on the provider and its response body carried
the outcome. Phase 8 decouples this: `create_command` enqueues a
`COMMAND_DISPATCH` job (`dedup_key` = the command's own id) and returns the
command still `PENDING`. The worker's `_process_command_dispatch` claims
it and calls `CommandService.process_dispatch_job`, which reuses
`_dispatch` completely unchanged — every Phase 6 safeguard (capability
check, WIPE credential revocation, REFRESH_INFO device-field sync, full
audit trail) fires exactly as before, just asynchronously.

`process_dispatch_job` guards against redispatch: if the command is no
longer `PENDING` when the job runs (a prior attempt already dispatched and
committed), it's a no-op. Combined with the job-level dedup_key, this
closes the double-dispatch risk for every case except a worker crashing
strictly between a successful provider call and the commit that would
have recorded it — an acknowledged, documented gap (the same category as
Phase 6's "cancel can never recall an already-accepted push"), not a
silently ignored one. See `docs/architecture/phase-8.md` §3 for the full
writeup and `tests/test_worker.py::test_command_dispatch_job_retry_does_not_redispatch`.

## 8. Multi-worker-safe claiming (Phase 8)

`BackgroundJobRepository.claim_next` now:

- Uses `SELECT ... FOR UPDATE SKIP LOCKED` when the session is bound to
  Postgres — two workers racing this query can never both select the same
  row; the second worker's SELECT simply skips a row the first has
  already locked. On SQLite (the test harness), it falls back to plain
  `FOR UPDATE`, which SQLite silently no-ops on — irrelevant there since a
  test process is single-writer anyway.
- Also claims a `CLAIMED` job whose lease has expired
  (`JOB_LEASE_TIMEOUT`, 10 minutes, computed from `claimed_at` — no stored
  expiry column, since a flat timeout is sufficient for the
  single-process-crash scenario this environment can actually exercise)
  — recovers a job stuck behind a crashed worker instead of leaving it
  `CLAIMED` forever. Each reclaim increments `attempts` like any other
  attempt, so a job that keeps crashing its worker still eventually hits
  `max_attempts` and goes `FAILED` rather than looping indefinitely.

**What's still unverified**: no live Postgres exists in this development
environment, so "two workers never claim the same row" is proven by the
`FOR UPDATE SKIP LOCKED` SQL construct being used at all — reviewed
against Postgres semantics — not by an actual concurrent-worker test.
`tests/test_worker.py::test_claim_next_recovers_a_lease_expired_claimed_job`
proves the lease-recovery half against SQLite, since that part doesn't
depend on real row-level locking.

## 9. Admin API (Phase 8)

`GET /jobs`, `GET /jobs/{id}` — flat, `X-Organization-ID`-scoped, gated by
`job.read` (granted to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN`/`AUDITOR`,
not `HELP_DESK`). `payload` is returned as-is — every job type's payload
is documented as containing only UUIDs, scope enums, and pagination
cursors, never a secret.

**Phase 10 added `POST /jobs/{id}/retry`** (gated by a separate
`job.retry` permission, not granted to `AUDITOR`) once a real need to act
on a dead-lettered job — not just read why it failed — showed up; see
`docs/architecture/phase-10.md` §4 for the retry semantics (only `FAILED`
jobs, `attempts`/`last_error` deliberately preserved so a manually-retried
job still re-exhausts `max_attempts` like an automatic one) and
`?command_id=` traceability (§5 there).

Every `background_jobs` row has a `NOT NULL organization_id` (there is no
system-wide job type — reconciliation stayed a plain sweep, never a job
row), so there's no "system job vs. tenant job" ambiguity for this API to
resolve: it's the same tenant-isolation rule as every other Phase 4+
resource, no exception needed.
