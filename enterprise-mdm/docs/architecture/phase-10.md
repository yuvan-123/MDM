# Phase 10 — Provider-Link Administration, Manual Retry, Traceability, Unified Health

Phase 9 wired real Android/Apple providers into dispatch and inbound
webhooks but left several operational gaps: `ProviderDeviceLink` could
only be created by a direct database write, a failed background job or
provider event had no recovery path short of a manual DB edit, an Apple
Phase 9 check-in bug silently corrupted device identity after the first
token update, and there was no single place to see whether Android/Apple
credentials were healthy. Phase 10 closes these five gaps. It does not
touch the provider abstraction, the dispatch state machine, or the
webhook/check-in authentication built in Phase 9 — only what's described
below.

**Verification status**: backend code is written and imports cleanly
(`python -c "import app.main"` succeeds; full `ast.parse` syntax check
passes across `app/`, `tests/`, `migrations/versions/`). Ruff/mypy have
**not** been run against Phase 10's new code, and **no pytest run has
happened yet** for any Phase 10 test file. Treat everything below as
"implemented and self-reviewed," not "verified" — see
`docs/security/phase-10-security-review.md` §4 for the current gap list.

## 1. Apple identity fix (a real Phase 9 bug, not cosmetic)

Phase 9's `provider.py::_require_push_identity` (outbound dispatch) and
the check-in `TokenUpdate` handler (inbound resolution) both read/wrote
the same `ProviderDeviceLink.provider_device_id` column, but the column
meant two genuinely different things depending on direction: the device's
UDID for inbound check-in resolution, Phase 6's original intent, versus
the device's current APNs push token for outbound dispatch. Every
`TokenUpdate` message therefore overwrote the UDID with a push token,
corrupting the resolution key for any *subsequent* check-in.

Fixed with a second, genuinely distinct column rather than a shared one:

- `app/provider_events/models.py::ProviderDeviceLink.apple_push_token`
  (nullable, Apple rows only) — the rotatable push token.
  `provider_device_id` now unambiguously means "the identifier a device
  authenticates itself with" for every provider (Android's device
  resource id; Apple's UDID) and nothing else ever overwrites it.
- `app/platform/apple/router.py`'s `TokenUpdate` handler now writes
  `apple_push_token`, never `provider_device_id`.
- `app/repositories/provider_device_link_repository.py::get_apple_push_token`
  (new) — Apple-specific outbound resolution, parallel to the existing
  `get_provider_device_id` every other provider uses.
- `app/commands/service.py::CommandService._dispatch` branches on
  `command.provider_name`: `"apple"` resolves through
  `get_apple_push_token`, everything else through the existing
  `get_provider_device_id`.
- `ProviderDeviceLinkSource` (new enum: `MANUAL` / `PROVIDER_ENROLLED`)
  records how a link was created. Every link created today is `MANUAL` —
  nothing in this codebase performs real Android Enterprise or Apple ADE
  enrollment, so `PROVIDER_ENROLLED` is a real value with no current
  producer, kept now so a later phase adding real enrollment doesn't need
  another migration on top of this one.

No live deployment of this codebase has ever existed, so no real
corrupted row is known to exist. The migration (§2) cannot distinguish "a
UDID" from "a push token accidentally left in the same column" and does
not attempt to guess — see the migration's own docstring.

## 2. Migration `0009_phase10_provider_link_admin`

Additive only, does not touch 0007/0008: adds `apple_push_token` and
`link_source` (+ its enum) to `provider_device_links`; adds 5 new
`audit_event_type` values (`PROVIDER_DEVICE_LINK_CREATED/UPDATED/DELETED`,
`BACKGROUND_JOB_RETRIED`, `PROVIDER_EVENT_RETRIED`) via
`ALTER TYPE ... ADD VALUE IF NOT EXISTS`, matching the 0003–0006
precedent. Downgrade drops the two new columns and the new enum but
cannot remove the added `audit_event_type` values — Postgres has no
`DROP VALUE`, same limitation every prior phase's downgrade already
documents.

## 3. ProviderDeviceLink admin API

Removes the Phase 9 "direct DB write only" limitation.
`app/provider_events/link_router.py` (flat, `X-Organization-ID`-scoped
like jobs/provider-events):

```
GET    /provider-device-links           provider_device_link.read   (?provider_name=&device_id=&search=, paginated)
GET    /provider-device-links/{id}      provider_device_link.read
POST   /provider-device-links           provider_device_link.create
PATCH  /provider-device-links/{id}      provider_device_link.update
DELETE /provider-device-links/{id}      provider_device_link.delete
```

`app/provider_events/link_service.py::ProviderDeviceLinkService` owns all
validation, each mutation audit-logged:

- `provider_name` must be one of `{android, apple, mock}` — not an
  exhaustive platform registry (none exists elsewhere either), just
  enough to reject an obvious typo before it creates an unreachable
  mapping.
- The target device must resolve through
  `DeviceRepository.get_for_organization` — a `device_id` that exists but
  belongs to a different organization 404s identically to one that
  doesn't exist at all, never confirming cross-tenant existence.
- Uniqueness is pre-checked with `exists_for_provider` (409 on collision)
  before insert; the database's own `uq_provider_device_link` constraint
  (`organization_id, provider_name, provider_device_id`) is the
  structural backstop regardless of what the pre-check found.
- `delete_link` removes only the link row — the underlying `Device` is
  never touched, so unlinking a provider mapping never deletes or
  otherwise affects the MDM device object itself.

RBAC: 4 new permissions (`provider_device_link.{read,create,update,delete}`)
granted in full to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN`, read-only to
`AUDITOR`, and not granted to `HELP_DESK` at all.

## 4. Job manual retry

`POST /jobs/{id}/retry`, gated by a new `job.retry` permission — kept
separate from the existing `job.read` because seeing a failure doesn't
imply authority to re-trigger it. Granted to `ORGANIZATION_ADMIN`/
`SECURITY_ADMIN` only, not `AUDITOR`, not `HELP_DESK`.

`app/jobs/service.py::JobService.retry_job` allows only `FAILED` jobs
(409 `JOB_NOT_RETRYABLE` otherwise — a `PENDING`/`CLAIMED` job is already
active, and retrying a `COMPLETED` job risks a real double-execution for
a `COMMAND_DISPATCH` job specifically). Audit-logged as
`BACKGROUND_JOB_RETRIED`.

`BackgroundJobRepository.retry` sets `status=PENDING`,
`scheduled_at=now`, `completed_at=None` — and **deliberately does not
reset `attempts` or clear `last_error`**. The next claim increments
`attempts` again exactly like an automatic retry would, so a job that
fails again still re-exhausts `max_attempts` rather than being retriable
forever; bounding "no infinite manual retry loop" needed no new logic,
it falls directly out of the existing attempts/max_attempts mechanism.

**Ripple**: `JobService.__init__` now requires `audit_repo` (previously
`job_repo`-only), touching every construction site across `app/` and
`tests/` — `commands/dependencies.py`, `jobs/dependencies.py`,
`policies/dependencies.py`, `provider_events/handlers.py`,
`workers/loop.py`, and 7 test files. This is the single largest
correctness risk in this phase — see the security review's gap list.

## 5. Command -> job traceability

`GET /jobs?command_id=...` (new query param, `job.read`-gated, same as
every other `/jobs` filter). `JobService.list_jobs` translates
`command_id` into `dedup_key=f"command_dispatch:{command_id}"` internally
— the exact convention `enqueue_command_dispatch` already uses — so the
caller never needs to know that convention exists.
`BackgroundJobRepository.list_for_organization`/`count_for_organization`
gained the underlying `dedup_key` filter param.

Provider-event -> command traceability needed no new plumbing: a
`COMMAND_COMPLETED`/`COMMAND_FAILED` event's payload already carries
`provider_command_id`, already visible via the existing
`GET /provider-events/{id}`.

## 6. Provider-event manual retry

`POST /provider-events/{id}/retry`, gated by a new
`provider_event.retry` permission (same role grants as `job.retry`).

`ProviderEventService.retry_event` allows only `FAILED` events (409
`PROVIDER_EVENT_NOT_RETRYABLE` otherwise) and re-runs `_process_event`
directly — **deliberately not** `ingest_event` — because the
`(provider_name, provider_event_id)` idempotency check exists to guard
against *redelivery of a duplicate event*, a different guarantee from an
explicit, single, admin-triggered re-attempt at processing the *same*
row. Audit-logged as `PROVIDER_EVENT_RETRIED`.

**Ripple**: `ProviderEventService.__init__` now also requires
`audit_repo` — 3 construction sites (`provider_events/dependencies.py`
and two test files), smaller blast radius than the job retry ripple.

## 7. Unified provider health

`GET /providers/status` — `app/platform/router.py`, gated by
`AUDIT_READ` (a platform-level permission, effectively SUPER_ADMIN),
same as the Phase 9 per-platform health endpoints it wraps.
`app/platform/health.py` factors the shared `{configured, ok, error}`
shape (`ProviderHealth` TypedDict) out of Android/Apple's individual
`/health` endpoints so this new aggregate endpoint and the two
per-platform ones share exactly one implementation of "never expose
credentials, tokens, or certificate material" and "never touch a real
device — only attempt to mint/reuse an auth token." Response:
`{android: ProviderHealth, apple: ProviderHealth}`. No test written yet
specifically for this endpoint; the per-platform Phase 9 tests already
cover the underlying `health_check()` logic it calls.

## 8. Wiring

`app/main.py` registers `provider_device_links_router` and
`provider_status_router`; version bumped to `"0.10.0"`.

## 9. What's genuinely done vs. outstanding

**Done, self-reviewed, syntax-checked, not yet test-verified:** all of
§1–§8 above, plus test files written for each
(`tests/test_provider_device_link_api.py`,
`tests/test_apple_checkin.py`'s corrected `TokenUpdate` test, retry/
traceability additions to `tests/test_job_api.py`, retry additions to
`tests/test_provider_event_api.py`).

**Not started:**
- Ruff/mypy against Phase 10's new code specifically.
- Any pytest run against any Phase 10 test file.
- Frontend: no provider-device-links admin page, no provider-health
  dashboard section, no retry buttons on the existing jobs/
  provider-events pages.
- This document's cross-links from `docs/architecture/background-jobs.md`
  and `docs/architecture/provider-events.md` noting these additions.
