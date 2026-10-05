# Phase 8 — Operationalized Async Infrastructure Security Review

## 1. Scope

The job/provider-event admin APIs, provider-event processing pipeline,
tenant-safe provider-device resolution, async command dispatch, and
multi-worker-safe job claiming introduced in Phase 8. Builds on
`docs/security/phase-7-security-review.md` and
`docs/security/phase-6-security-review.md` — this review focuses on
what's new. Full design rationale in `docs/architecture/phase-8.md`,
`docs/architecture/background-jobs.md`, and
`docs/architecture/provider-events.md`.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Job admin API tenant isolation** | `GET /jobs`/`GET /jobs/{id}` filter on `organization_id` via the same `require_current_organization_permission` header-based pattern as every other Phase 4+ resource; `BackgroundJobRepository.get_for_organization`/`list_for_organization` are the only queries the router can reach — `claim_next` (the one legitimately cross-tenant query) is never exposed through it. | `tests/test_job_api.py::test_org_a_cannot_see_org_b_jobs` | PASS |
| **Provider-event admin API tenant isolation** | Same pattern; additionally an unresolved (`organization_id=NULL`) event is invisible through this API for every organization, not leaked to whichever org asks first. | `tests/test_provider_event_api.py::test_org_a_cannot_see_org_b_provider_events`, `test_unresolved_organization_event_is_invisible_to_every_org` | PASS |
| **Job/event admin API RBAC** | New `job.read`/`provider_event.read` permissions, granted to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN`/`AUDITOR` only — deliberately excluded from `HELP_DESK` (operational infrastructure, not that role's device-facing scope). | `tests/test_job_api.py::test_help_desk_cannot_read_jobs`, `tests/test_provider_event_api.py::test_help_desk_cannot_read_provider_events` | PASS |
| **No write surface on either admin API** | Both routers are GET-only — no retry/cancel/reprocess endpoint exists, so there is no new mutating attack surface to reason about this phase. | Code review (`app/jobs/router.py`, `app/provider_events/router.py` — no POST/PATCH/DELETE routes) | PASS |
| **Provider-device resolution is tenant-safe by construction** | `ProviderDeviceLink`'s unique constraint is `(organization_id, provider_name, provider_device_id)` — resolution can never return an ambiguous or cross-tenant match; it structurally cannot, not merely by caller discipline. Resolution is additionally gated on the caller supplying `organization_id` at all. | Code review (`app/provider_events/models.py::ProviderDeviceLink`, `ProviderDeviceLinkRepository.get_device_id`) + `tests/test_provider_events.py::test_ingest_event_skips_device_resolution_without_organization_id` | PASS |
| **Provider events cannot forge command completion** | `CommandService.apply_provider_event` only accepts `SENT -> terminal`, resolves the command by `provider_command_id` scoped to the event's own `organization_id`, and is the *only* place either command-completion handler writes to `DeviceCommand`. A command in any other state, or a `provider_command_id` from a different organization, is rejected, not silently coerced. | `tests/test_provider_events.py::test_command_failed_event_requires_command_still_sent`, `test_command_completed_event_without_organization_fails_safely` | PASS |
| **Redelivered events cannot double-complete a command** | `(provider_name, provider_event_id)` idempotency check runs *before* device resolution or handler dispatch — a redelivered `COMMAND_COMPLETED` event returns the original row unchanged, no second `apply_provider_event` call, no second audit event. | `tests/test_provider_events.py::test_command_completed_event_redelivery_does_not_double_complete` | PASS |
| **Async command dispatch preserves every Phase 6 safeguard** | `process_dispatch_job` calls `_dispatch` completely unchanged — capability checks, WIPE's immediate credential revocation, REFRESH_INFO's field-allowlist sync, and full audit logging all fire identically to the pre-Phase-8 synchronous path, just later. Nothing about *what* dispatch does changed, only *when*. | `tests/test_command_wipe_safeguards.py` (updated, still passing against the async flow) | PASS |
| **Dispatch job cannot be forced to re-execute a completed command** | `process_dispatch_job` checks `command.status == PENDING` before calling `_dispatch`; a job retried after a prior successful, committed attempt is a no-op. Combined with the job's own `dedup_key` (one active `COMMAND_DISPATCH` job per command), this closes the double-dispatch risk for every case except a worker crash strictly between a successful provider call and the commit that would have recorded it — documented, not silently assumed safe (`docs/architecture/phase-8.md` §3). | `tests/test_worker.py::test_command_dispatch_job_retry_does_not_redispatch` | PASS |
| **Multi-worker job claiming** | `claim_next` uses `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres — two workers can never both claim the same row. A crashed worker's stuck `CLAIMED` job is recoverable via a lease timeout rather than blocking that job forever. | Code review (Postgres semantics) + `tests/test_worker.py::test_claim_next_recovers_a_lease_expired_claimed_job` (SQLite, lease-recovery half only — see gap below) | PASS (with noted gap) |
| **No secrets in any new payload/response surface** | `BackgroundJob.payload` (UUIDs/scope enums/cursors only), `ProviderEvent.payload` ("must never contain provider credentials", enforced at ingestion, unchanged from Phase 7) — both admin APIs return these as-is, matching the existing `DeviceCommand.result` precedent of trusting the write-time invariant rather than re-sanitizing on read. | Code review (`app/jobs/models.py`, `app/provider_events/models.py`) | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **Making command dispatch asynchronous broke the implicit "the POST
   response carries the outcome" assumption in ~15 existing Phase 6
   tests.** Not a security issue, but a correctness/compatibility one
   worth recording here since it touches the same code path as several
   security-relevant tests (`test_command_wipe_safeguards.py`,
   `test_command_platform_provider_unavailable.py`). Every affected test
   was updated (not weakened or deleted) to drive dispatch explicitly via
   `tests/conftest.py::dispatch_pending_command` and assert on the real
   outcome — see `docs/architecture/phase-8.md` §3 for the full list and
   rationale.

## 4. Known Gaps / Deferred

- **Genuine multi-worker concurrent-claim safety is unverified** against
  a live Postgres — no live Postgres in this development environment,
  same as every prior phase's gap. The `FOR UPDATE SKIP LOCKED` construct
  is correct by Postgres semantics on code review; it has not been
  exercised by two actual concurrent worker processes.
- **The dispatch-retry residual window** (a worker crash between a
  successful provider call and the commit recording it) is not solvable
  without provider-side idempotent request IDs, which no provider in this
  codebase has ever been called live to verify — documented as an
  accepted gap rather than fabricating a fix (`docs/architecture/phase-8.md`
  §3), consistent with the "no fake provider integration" rule this phase
  was built under.
- **No manual job-retry admin action** — Phase 7's automatic
  retry-with-backoff already covers transient failures; a write endpoint
  for forcing a retry is deferred until there's a real operational need
  for one beyond what the read-only status already shows.
- **Provider-event processing handlers are exercised only by
  directly-constructed test events** — no real Android/Apple traffic
  exists to produce a `COMMAND_COMPLETED`/`DEVICE_CHECK_IN`/etc. event, so
  the handler registry is correct in shape and unit-tested, not
  live-integration-proven. Same category of gap as every provider-facing
  claim since Phase 6.
- The pre-existing absence of real Android/Apple credentials, Docker, and
  genuine platform enrollment in this environment are unchanged from
  Phase 6/7 and continue to limit what provider-device resolution and
  real-provider dispatch can actually do in practice.
