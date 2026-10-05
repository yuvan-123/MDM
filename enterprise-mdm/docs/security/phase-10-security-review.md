# Phase 10 — Provider-Link Administration & Retry Security Review

## 1. Scope

The `ProviderDeviceLink` admin CRUD API, manual job/provider-event retry
endpoints, command-job traceability, the unified provider-health
endpoint, and the Apple UDID/push-token identity fix introduced in Phase
10. Builds on `docs/security/phase-9-security-review.md` and
`docs/security/phase-8-security-review.md` — this review focuses on
what's new. Full design rationale in `docs/architecture/phase-10.md`.

**This review is code-review-only.** Unlike Phase 8/9's security reviews,
no automated test in this phase has actually been run yet (see §4) —
every "PASS" below reflects the intended, implemented behavior read
directly from the source, not a green test run. Treat this table as a
pre-verification checklist, not a completed audit.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Cross-tenant device linking rejected** | `ProviderDeviceLinkService.create_link` resolves the target device through `DeviceRepository.get_for_organization` — a device belonging to a different organization 404s (`DEVICE_NOT_FOUND`) identically to a nonexistent device, never confirming cross-tenant existence. | `tests/test_provider_device_link_api.py` (cross-org device rejected) — **written, not yet run** | PENDING |
| **Duplicate provider mapping rejected, not silently overwritten** | `exists_for_provider` is checked before insert (409 `PROVIDER_DEVICE_LINK_ALREADY_EXISTS`); the database's `uq_provider_device_link` constraint (`organization_id, provider_name, provider_device_id`) is the structural backstop if the pre-check is ever raced. | `tests/test_provider_device_link_api.py` (duplicate mapping rejected) — **written, not yet run** | PENDING |
| **Unlinking a device never deletes the device itself** | `ProviderDeviceLinkService.delete_link` calls `ProviderDeviceLinkRepository.delete(link)` only — no code path in this service ever touches the `Device` row. | `tests/test_provider_device_link_api.py` (safe unlink doesn't delete device) — **written, not yet run** | PENDING |
| **Cross-org read/write of another org's links rejected** | Every link route resolves through `org_context.organization_id` from the `X-Organization-ID` header, and `get_link`/`update_link`/`delete_link` all route through `get_for_organization` first — an org B link_id presented under org A's header 404s. | `tests/test_provider_device_link_api.py` (org A can't see/modify org B links) — **written, not yet run** | PENDING |
| **HELP_DESK cannot manage provider links** | `HELP_DESK`'s permission set (`app/rbac/permissions.py`) includes none of the 4 new `provider_device_link.*` permissions — confirmed by direct inspection of the role definition. `AUDITOR` gets only `provider_device_link.read`. | `tests/test_provider_device_link_api.py` (HELP_DESK denied, AUDITOR read-only) — **written, not yet run** | PENDING |
| **Job retry cannot be used to re-run an already-successful or already-active job** | `JobService.retry_job` only permits `status == FAILED` (409 `JOB_NOT_RETRYABLE` otherwise) — blocks retrying a `PENDING`/`CLAIMED` job (already active) and a `COMPLETED` `COMMAND_DISPATCH` job specifically (would risk a real double-dispatch to a device). | `tests/test_job_api.py` (cannot retry non-FAILED) — **written, not yet run** | PENDING |
| **Manual retry cannot become an infinite bypass of `max_attempts`** | `BackgroundJobRepository.retry` deliberately does not reset `attempts` or clear `last_error` — the next claim increments `attempts` exactly like an automatic retry, so a job that keeps failing still re-exhausts `max_attempts` under repeated manual retries, not just automatic ones. | Code review (`app/repositories/background_job_repository.py::retry`) — no test specifically drives a job through `max_attempts` manual retries to confirm it eventually stays FAILED | PENDING (reasoning only) |
| **Job/event retry requires a permission distinct from read** | `job.retry` and `provider_event.retry` are separate permissions from `job.read`/`provider_event.read` — granted only to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN`; `AUDITOR` (which has both `*_READ` permissions) has neither `*_RETRY` permission, confirmed by direct inspection of the role definitions. | `tests/test_job_api.py`, `tests/test_provider_event_api.py` (AUDITOR cannot retry, 403) — **written, not yet run** | PENDING |
| **Cross-org job/event retry rejected** | Both `retry_job`/`retry_event` call `get_job`/`get_event` first, which route through the same org-scoped repository lookup every other job/event endpoint uses. | `tests/test_job_api.py`, `tests/test_provider_event_api.py` (org A cannot retry org B's job/event) — **written, not yet run** | PENDING |
| **Provider-event manual retry does not bypass the idempotency guarantee it looks similar to** | `retry_event` calls `_process_event` directly, never `ingest_event` — the `(provider_name, provider_event_id)` uniqueness constraint that prevents duplicate *redelivery* is untouched and still applies to every other ingestion path; retry only re-runs processing on a row that already passed that check once. | `tests/test_provider_event_api.py::test_retry_can_succeed_once_the_underlying_condition_is_fixed` (seeds a FAILED event referencing a not-yet-existing command, creates the command, retries, proves it now completes) — **written, not yet run** | PENDING |
| **Apple identity fix does not weaken tenant/provider scoping of dispatch resolution** | `CommandService._dispatch`'s new Apple/non-Apple branch still resolves through the same org-scoped `ProviderDeviceLinkRepository` methods (`get_apple_push_token`/`get_provider_device_id`), both keyed by `command.provider_name` so a link created for the wrong provider can never resolve — this phase changes *which column* is read for Apple, not the tenant/provider scoping logic itself. | `tests/test_apple_checkin.py::test_checkin_token_update_stores_push_token_without_corrupting_udid` — **written, not yet run** | PENDING |
| **Provider-health endpoint never exposes credentials, tokens, or certificate material** | `app/platform/health.py`'s `ProviderHealth` TypedDict returns only `{configured, ok, error}`; `error` is `AppError.message` text (e.g. an HTTP status), never service-account JSON, private key bytes, or a minted token — same shape and same rule as the Phase 9 per-platform endpoints this one wraps. | Code review (`app/platform/health.py`, `app/platform/router.py`) | PASS |
| **Unified provider-health endpoint is platform-scoped, not org-bypassable** | Gated by `require_permission(p.AUDIT_READ)` — a platform-level check via `CurrentUser.has_permission`, not the `X-Organization-ID` header pattern every org-scoped Phase 10 endpoint uses. An org-scoped `ORGANIZATION_ADMIN` grant does not satisfy it; only a genuine platform role (SUPER_ADMIN) does — identical gating to the Phase 9 per-platform `/health` endpoints it aggregates. | Code review (`app/platform/router.py`) — no dedicated test written yet for this specific endpoint (§4) | PASS (reasoning only) |
| **Health check never mutates device or command state** | `check_android_health`/`check_apple_health` only attempt to mint (or reuse a cached) auth token via each client's `health_check()` — no code path here calls `issueCommand`, `patch`, or any APNs push. | Code review (`app/platform/health.py`) | PASS |
| **Audit log entries created for every provider-link and retry mutation** | `create_link`/`update_link`/`delete_link` and `retry_job`/`retry_event` each call `AuditRepository.log_event` with the actor, organization, and a metadata dict identifying the affected row — before returning to the caller, so a mutation is never silently unaudited even on the success path. | Code review (`app/provider_events/link_service.py`, `app/jobs/service.py`, `app/provider_events/service.py`) — audit-log assertions are embedded in the retry/CRUD tests above rather than a standalone audit test | PENDING (bundled in PENDING tests above) |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **Two required-constructor-parameter additions rippled across a large
   number of call sites.** `JobService.__init__` and
   `ProviderEventService.__init__` both gained a required `audit_repo`
   parameter (needed so retry can audit-log). `JobService`'s ripple
   touched ~13 sites (`commands/dependencies.py`, `jobs/dependencies.py`,
   `policies/dependencies.py`, `provider_events/handlers.py`,
   `workers/loop.py`, and 7 test files); `ProviderEventService`'s touched
   3. All were updated via a batch script plus manual cleanup of sites
   the regex missed (`app/jobs/dependencies.py`'s keyword-arg
   construction; a pre-extracted `job_repo` variable in
   `tests/test_worker.py` around line 308). **This is the single largest
   correctness risk in this phase** — a missed or malformed call site
   would surface as an import-time or fixture-time `TypeError`, not a
   subtle logic bug, but it has not yet been proven absent by an actual
   test run. Flagged explicitly for the first pytest pass.
2. **The Apple UDID/push-token column conflation Phase 9 explicitly
   deferred** (`docs/architecture/phase-9.md` §3/§6) turned out to be an
   active bug, not just a documented inconsistency: every `TokenUpdate`
   check-in was actively corrupting the UDID Apple check-in resolution
   depends on. Resolved with a second column (`apple_push_token`) rather
   than continuing to defer it, since Phase 10's own admin CRUD API would
   otherwise let an operator create a syntactically valid link that
   outbound dispatch could silently break on the very next check-in.
3. **Migration cannot repair any real corrupted row.** Recognized during
   migration design: since no live deployment of this codebase has ever
   existed, there's no way to distinguish, from data alone, "this
   `provider_device_id` is a real UDID" from "this is a push-token string
   left over from the Phase 9 bug." Documented explicitly in the
   migration's own docstring rather than writing a guessing heuristic.

## 4. Known Gaps / Deferred

- **No pytest run has happened for any Phase 10 test file** — every
  "PASS"/"PENDING" row above marked PENDING is implemented-and-reviewed
  behavior, not confirmed behavior. Given the scale of the ripple edits
  in §3.1, this is the top-priority gap to close before trusting this
  phase.
- **Ruff/mypy have not been run against any Phase 10 file** — only a
  syntax check (`ast.parse`) and an import check
  (`python -c "import app.main"`) have been done directly.
- **No dedicated test for `GET /providers/status`** — the endpoint is
  covered only indirectly, through the already-tested per-platform
  `health_check()` logic it calls.
- **No automated test drives a job through repeated manual retries to
  `max_attempts` exhaustion** — the "cannot become an infinite bypass"
  claim in §2 is verified by code review of the shared attempts/
  max_attempts mechanism, not by a dedicated retry-exhaustion test.
- **Migration cannot repair a real Phase 9 identity-corruption row** —
  by design; see §3.3. Any real corrupted row (none is known to exist)
  would need manual review before this phase's fix takes effect for it.
- **Frontend has no permission-aware UI yet** for any Phase 10 capability
  — no retry buttons, no provider-device-links admin page, no
  provider-health dashboard section. A 403 from a role lacking
  `job.retry`/`provider_event.retry`/`provider_device_link.*` is fully
  enforced server-side regardless, but nothing in the UI reflects that
  yet.
- Every other gap already documented in
  `docs/security/phase-6-security-review.md` §4,
  `docs/security/phase-8-security-review.md` §4, and
  `docs/security/phase-9-security-review.md` §4 (no live Postgres, no
  real credentials, no Docker, no real device enrollment) is unchanged
  and still applies.
