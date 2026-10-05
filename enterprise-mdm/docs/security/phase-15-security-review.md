# Phase 15 — Production Readiness Security Review

## 1. Scope

Final code-level security review across authentication, authorization,
tenant isolation, CSRF, CORS, secrets, webhooks, destructive commands,
compliance remediation, application deployment, audit logging, and rate
limiting — per brief §35. This is a review of the *existing* system
(Phases 1-14) plus verification of the new Phase 15 hardening described
in `docs/architecture/phase-15.md`. Per this phase's own honesty rule
(§45): every row below is marked with what was actually done —
IMPLEMENTED, VERIFIED LOCALLY, or BLOCKED BY EXTERNAL INFRASTRUCTURE —
never claimed as more than that.

**Code-review-only for anything not backed by a passing test.** Three new
targeted test files exist for this phase's own new code
(`tests/test_production_config_validation.py`,
`tests/test_security_headers_phase15.py`,
`tests/test_readiness_phase15.py`, `tests/test_rate_limiting_phase15.py`);
every row tied to one is marked PENDING until the user runs them.

## 2. Static verification actually performed

- `python -c "import app.main"` — succeeds.
- `python -m ruff check .` — **all checks passed**, for the first time
  running this exact command (matching CI's real scope) rather than the
  narrower `ruff check app [tests]` every prior phase's own review used.
  See `docs/architecture/phase-15.md` §5 for what this surfaced and how
  it was fixed (config only, zero file rewrites).
- `python -m mypy .` — **success, 164 source files**, same "first time at
  CI's real scope" story as ruff above.
- `python -m alembic history` — confirms a single linear, unbranched
  migration chain, `<base>` through `0013_phase14_applications (head)`.

## 3. Security Assessment Summary — review of existing system (Phases 1-14)

| Threat / Control Area | Finding | Verification Method | Status |
|---|---|---|---|
| **Cross-tenant device/job/provider-link/application access** | Every repository this review re-inspected (`DeviceRepository`, `BackgroundJobRepository`, `ProviderDeviceLinkRepository`, `ApplicationRepository`/`ApplicationAssignmentRepository`/`ApplicationDeploymentRepository`) takes `organization_id` and filters on it in every query — no bare `get_by_id` exists anywhere in `app/repositories/`. Confirmed by re-reading each repository file, not just their own phase's original review. | Code review (grep-equivalent: no `get_by_id(` without an `organization_id` parameter in any repository) | PASS |
| **Destructive commands (WIPE/UNENROLL/application removal) require confirm+idempotency_key** | `IssueWipeCommandRequest`/`IssueUnenrollCommandRequest` (`app/commands/schemas.py`) both require `confirm: Literal[True]` and a non-optional `idempotency_key` — re-confirmed unchanged since Phase 6/12. Application removal (`POST /applications/{id}/devices/{device_id}/remove`, Phase 14) has no separate confirm flag — flagged as a genuine gap below (§4.1). | Code review | PASS (commands) / FINDING (app removal, see §4.1) |
| **Webhook authentication (Android Pub/Sub, Apple check-in)** | Android: `android_pubsub_verification_token` unset means the endpoint rejects every request (fails closed) — re-confirmed in `app/platform/android/webhook.py`. Apple: check-in resolution requires an existing `ProviderDeviceLink` match (org-scoped), never trusts a bare device-supplied identifier. Neither webhook path was modified this phase. | Code review | PASS |
| **CSRF exemption list is still minimal and justified** | Re-read `app/auth/middleware.py::EXEMPT_PATHS`/`EXEMPT_PATH_PATTERNS` in full — every entry still carries its own non-cookie authentication (enrollment token, device credential, webhook token/link resolution, public single-use token). No new exemption was added this phase. | Code review | PASS |
| **Account lockout / password reset / session revocation** | Re-confirmed database-authoritative (not Redis-authoritative) failed-login counter, existing session-revocation-on-password-change behavior. Unchanged this phase. | Code review | PASS |
| **RBAC role boundaries (HELP_DESK/AUDITOR vs admin roles)** | Re-read `app/rbac/permissions.py` in full — `HELP_DESK` still lacks every destructive/write permission (`device.wipe`, `application.deploy`, `policy.*`, `compliance.remediation.manage`, etc.); `AUDITOR` remains read-only. Unchanged this phase. | Code review | PASS |

## 4. New findings from this phase's own review

### 4.1 Application removal has no `confirm` safeguard (genuine gap, not fixed this phase)

`POST /applications/{id}/devices/{device_id}/remove`
(`app/applications/router.py::remove_application_from_device`, Phase 14)
dispatches a real `UNINSTALL_APPLICATION` command with no request body at
all — unlike `WIPE`/`UNENROLL`, there is no `confirm: Literal[True]` flag
and no caller-supplied `idempotency_key` (the service layer generates its
own, keyed by attempt count). This is meaningfully less destructive than
a device wipe (removing one managed app, not erasing the device), but is
still a real state change with no double-confirmation. **Not fixed this
phase** — scope was capped at genuinely new Phase 15 infrastructure
concerns rather than reopening Phase 14's API surface; flagged here
explicitly rather than silently left for a future reviewer to
rediscover. Recommended for Phase 16 (or a Phase 14 follow-up) if one is
ever opened: add an optional `confirm`/`idempotency_key` body matching the
`IssueWipeCommandRequest` shape.

### 4.2 `csrf_secret` is unused, dead configuration

Discovered while writing `validate_production_config` (§3 of the
architecture doc) — `app/auth/middleware.py::CSRFMiddleware` never reads
`Settings.csrf_secret`; the actual CSRF check is a double-submit cookie
comparison needing no secret. Not a vulnerability (the double-submit
design is sound on its own), but worth flagging: an operator who diligently
rotates `CSRF_SECRET` believing it strengthens CSRF protection is doing
inert work. Documented, not removed (removing a public `Settings` field
is outside this phase's scope).

## 5. Security Assessment Summary — new Phase 15 hardening

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Insecure default secrets/cookie/CORS config cannot silently reach production** | `validate_production_config` (`app/core/config.py`) raises `ProductionConfigError` at process startup — before the ASGI app is even constructed — for any of: insecure default `JWT_SECRET`/`CSRF_SECRET`/`SEED_ADMIN_PASSWORD`, `JWT_SECRET` under 32 chars or equal to `CSRF_SECRET`, `COOKIE_SECURE=false`, or a wildcard/localhost `CORS_ORIGINS` entry — only when `APP_ENV=production`. | `tests/test_production_config_validation.py` (9 cases: secure config passes, non-production is a no-op, each of 3 insecure defaults individually rejected, short/reused secret rejected, insecure cookie rejected, 3 unsafe CORS origin shapes rejected, multiple violations all reported together) — **written, not yet run** | PENDING |
| **A Redis outage cannot take the API out of service over a dependency it already treats as best-effort** | `/ready`'s `overall_ok` now depends only on `check_database_connection()` — Redis status is still reported for visibility but no longer gates the HTTP status code. | `tests/test_readiness_phase15.py` (4 cases: db+redis ok, db ok+redis down -> still 200, db down+redis ok -> 503, both down -> 503) — **written, not yet run** | PENDING |
| **Baseline response headers present on every response, including error responses** | `SecurityHeadersMiddleware` (new) sets `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`/`X-Permitted-Cross-Domain-Policies` unconditionally, `Strict-Transport-Security` only when `cookie_secure` is true. | `tests/test_security_headers_phase15.py` (headers present on 200 and 404 responses, HSTS absent under the test harness's default `cookie_secure=False`) — **written, not yet run** | PENDING |
| **Public, unauthenticated device-registration endpoint is not an unthrottled brute-force target** | `check_enrollment_rate_limit` (new, `app/auth/rate_limit.py`) — same fixed-window Redis counter, same fail-open-on-Redis-unavailable behavior as the pre-existing login limiter, wired into `POST /enrollment/register`. | `tests/test_rate_limiting_phase15.py` (fails open with no Redis reachable — genuinely exercised, this sandbox has no Redis; blocks after a mocked threshold; allows under threshold; end-to-end smoke test confirms the new call doesn't itself break a normal registration attempt) — **written, not yet run** | PENDING |
| **A worker container can be gracefully drained, not hard-killed, on shutdown** | `scripts/worker.py` now wires `SIGTERM`/`SIGINT` to the `stop_event` `run_worker_loop` already accepted since Phase 7. `app/workers/loop.py` itself is unchanged. | Code review (`run_worker_once` already only ever completes one job fully per cycle before the loop re-checks its stop condition, making cooperative shutdown safe by construction — no new test needed to demonstrate a property that already followed from unchanged Phase 7 code) | PASS (reasoning only) |
| **Migration environment discovers every model** | `migrations/env.py` now imports `app.applications.models` (Phase 14's module, previously missing from this list). | Code review + `alembic history` (chain intact, single head) | PASS |
| **CI's actual lint/typecheck commands are clean** | `ruff check .` / `mypy .` — the literal CI commands — both pass, via `pyproject.toml` per-file-ignores/exclude additions touching zero test/migration file content. | Ran both commands directly this session | PASS (locally; CI itself has not re-run since this change — see §6) |

## 6. Known Gaps / Deferred / Blocked

- **No pytest run has happened for any of the four new Phase 15 test
  files** — every PENDING row above reflects implemented-and-reviewed
  behavior, not confirmed behavior.
- **Real `alembic upgrade head` against Postgres**: BLOCKED BY EXTERNAL
  INFRASTRUCTURE — no Docker/Postgres in this dev environment. Added as a
  permanent CI step; genuinely verified only on the next real CI run,
  which this session cannot trigger or observe.
- **CI itself has not been re-run** since the `pyproject.toml`/workflow
  changes in this phase — "both pass locally" (§5's last row) is not the
  same claim as "CI is green"; that requires an actual push/PR, which is
  outside this session's ability to perform or observe (no git repository
  in this working directory at all — see `docs/architecture/phase-15.md`).
- **Application-removal confirm safeguard** (§4.1) — genuine, documented,
  unfixed gap.
- **No real backup/restore cycle was performed** — `docs/operations/
  disaster-recovery.md` documents the procedure; it has not been executed
  against real infrastructure this session had no access to.
- **No real multi-worker concurrent-claim test, no real Android/Apple
  device/credential test** — unchanged, longstanding limitation
  documented by every phase since Phase 6/9.
- Every other gap already documented in `docs/security/phase-6-security-review.md`
  §4, `docs/security/phase-12-security-review.md` §5,
  `docs/security/phase-13-security-review.md` §5, and
  `docs/security/phase-14-security-review.md` §5 is unchanged and still
  applies.
