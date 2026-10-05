# Phase 7 — Transaction Fix, Background Jobs & Provider Events Security Review

## 1. Scope

The transaction-commit fix, the `background_jobs` worker/reconciliation
system, and provider-event ingestion introduced in Phase 7. Builds on
`docs/security/phase-6-security-review.md` and
`docs/security/multi-tenancy-security-review.md` — this review focuses on
what's new. Full design rationale in
`docs/architecture/transaction-management.md`,
`docs/architecture/background-jobs.md`, and
`docs/architecture/provider-events.md`.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Data durability (transaction fix)** | `get_db_session` now commits on success / rolls back on failure — the single most severe pre-existing gap in this codebase, silently discarding every write in a real (Postgres) deployment across all six prior phases. | `tests/test_transaction_persistence.py` (real dependency, two separate session instances, fix reverted-then-restored to prove the test actually detects the bug) | PASS |
| **Worker-loop tenant isolation** | `BackgroundJobRepository.claim_next` and `DeviceCommandRepository.list_expired_sent_unscoped` are the only cross-tenant queries added this phase — both are system-level (no caller-supplied `organization_id` to scope by), mirroring the pre-existing `DeviceRepository.get_by_id_unscoped` precedent, and every job/command they touch still carries its own `organization_id` which downstream processing (`ComplianceService`, `CommandService`) uses correctly. | Code review (`app/repositories/background_job_repository.py`, `app/repositories/device_command_repository.py`) | PASS |
| **Compliance re-evaluation stays within its triggering organization** | `JobService.enqueue_policy_reevaluation` and the fanout processor never take `organization_id` from job payload data alone — every job row's `organization_id` column is set once at creation from the caller's already-validated tenant context (`PolicyService`'s org-scoped assignment methods, or the heartbeat's own `device.organization_id`), and every device page query filters on it. | Code review (`app/jobs/service.py`, `app/workers/loop.py`) | PASS |
| **No admin-facing surface added for jobs/events** | Neither `background_jobs` nor `provider_events` has a router — both are internal/worker-only and callable-service-only respectively (Phase 8 deferred). No new permission strings, no new attack surface exposed to authenticated org members this phase. | Code review (`app/jobs/`, `app/provider_events/` — no `router.py` in either) | PASS |
| **Provider-event device resolution is organization-gated** | `ProviderEventService.ingest_event` only attempts device resolution when the caller supplies `organization_id` — resolving a `provider_device_id` without one would be a genuine cross-tenant ambiguity (device identifiers are only unique per organization), not just an edge case. | `tests/test_provider_events.py::test_ingest_event_skips_device_resolution_without_organization_id` | PASS |
| **Provider-event idempotency prevents duplicate-processing** | Unique constraint on `(provider_name, provider_event_id)`; redelivery returns the original row unchanged, including its already-resolved `device_id` — a later redelivery can't be used to re-trigger resolution against a device that has since been reassigned/renamed. | `tests/test_provider_events.py::test_ingest_event_is_idempotent_on_redelivery` | PASS |
| **No secrets in job payloads or provider-event payloads** | `BackgroundJob.payload` carries only UUIDs/scope enums/pagination cursors; `ProviderEvent.payload` is documented as "must NEVER contain provider credentials" — Android/Apple credentials remain `Settings`-sourced only, unchanged from Phase 6. | Code review (`app/jobs/models.py`, `app/provider_events/models.py`) | PASS |
| **Job failures never crash the worker loop** | `run_worker_once` wraps job processing in a blanket `except Exception`, classifies via `is_transient`, and always commits — an unanticipated exception in one job's processing can't take down the loop or leave a job stuck `CLAIMED` forever. | Code review (`app/workers/loop.py::run_worker_once`) | PASS |
| **Retry classification fails safe** | `is_transient` treats any exception it doesn't specifically recognize as a permanent `AppError` code as *retryable* — an unanticipated failure mode gets retried (bounded by `max_attempts`) rather than a job silently vanishing into `FAILED` on the first unexpected error. | Code review (`app/jobs/retry.py`) | PASS |
| **Command reconciliation reuses verified expiration logic** | `reconcile_expired_sent_commands` reuses `_effective_command_status`'s expiration semantics unchanged — no new expiration rule was introduced, only a proactive trigger for a transition that already existed and was already tested (Phase 6). | `tests/test_worker.py::test_run_worker_once_reconciles_expired_sent_commands` | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **`migrations/env.py` was independently missing `import app.commands.models`**
   since Phase 6 — meaning `device_commands` has been invisible to
   `Base.metadata`/`alembic revision --autogenerate` for an entire phase,
   existing in a real database only via 0006's hand-written DDL. Not a
   Phase 7 regression, but caught and fixed while adding this phase's own
   model imports to the same file (`app.jobs.models`,
   `app.provider_events.models`) — see
   `docs/architecture/background-jobs.md` §6.

## 4. Known Gaps / Deferred to Phase 8

- **Genuine multi-worker concurrent-claim safety is unverified** — no live
  Postgres in this development environment (confirmed again at Phase 7
  start); `BackgroundJobRepository.claim_next` is a plain
  `SELECT`-then-`UPDATE`, not `FOR UPDATE SKIP LOCKED`. A single worker
  process is correct; two workers racing on the same row is not proven
  either way.
- **Postgres-specific transaction/locking behavior for the transaction fix
  itself** remains verified only against a file-backed SQLite engine (§2).
- **No admin-facing HTTP endpoints** for inspecting, retrying, or
  cancelling background jobs, or for querying ingested provider events.
- **No real webhook entry point** for provider events — see
  `docs/architecture/provider-events.md` §6 for the full list of
  event-ingestion gaps.
- **Editing an already-ACTIVE policy's configuration** does not re-trigger
  compliance evaluation for its existing assignees — only assignment
  changes do (`docs/architecture/background-jobs.md` §5).
- The pre-existing absence of real Android/Apple credentials and Docker in
  this environment, and the synthetic (non-platform-native) nature of
  Phase 5 enrollment, are unchanged from Phase 6 — both continue to limit
  what device resolution and real-provider dispatch can actually do, as
  documented in `docs/architecture/device-commands.md` §7 and
  `docs/architecture/provider-events.md` §4.
