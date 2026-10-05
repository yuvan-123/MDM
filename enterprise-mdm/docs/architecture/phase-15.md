# Phase 15 — Production Readiness, Hardening & Final Operational Readiness

Builds on completed Phases 1-14 (see `docs/architecture/phase-14.md` for
the most recent). Repo root: `enterprise-mdm/`. Brief: a 48-section
prompt whose explicit objective is NOT another feature — it's making the
existing system "safe, observable, recoverable, deployable, and
operationally ready for production," reviewing what already exists and
fixing only genuine gaps.

## 1. Method

Per the brief's own workflow (read memory -> inspect existing
infrastructure -> identify gaps -> implement -> targeted validation), this
phase inspected `app/core/config.py`, `app/database/session.py`,
`app/core/redis.py`, `app/main.py`, every middleware, `app/api/v1/health.py`,
`app/platform/health.py`/`router.py`, `app/auth/rate_limit.py`,
`scripts/worker.py`, `docker-compose.yml`, both Dockerfiles,
`.github/workflows/ci.yml`, and `migrations/env.py` before writing a
single line — most of this infrastructure turned out to already be
well-designed (see §2, "What was already right"); the fixes below are
exactly the gaps that inspection surfaced, not a rewrite.

## 2. What was already right (left unchanged)

- **`/health` vs `/ready` split** (`app/api/v1/health.py`) — already
  exactly the liveness/readiness distinction §11/§12 ask for.
- **Provider health already separate from application health**
  (`GET /providers/status`, `app/platform/router.py`) — never touches
  `/ready`, satisfying §13 without any change.
- **Redis usage is already fail-open** (`app/auth/rate_limit.py`) — every
  rate-limit helper already catches a Redis error and lets the request
  through rather than blocking it; account lockout is already
  database-authoritative, not Redis-authoritative. §24's "must fail
  safely if Redis becomes unavailable, persistent state belongs in
  Postgres" was already true before this phase.
- **Transaction/session architecture** (`app/database/session.py`) — the
  Phase 6/7 single-commit-point-per-request design (see that module's own
  docstring and `docs/architecture/transaction-management.md`) is correct
  and was not touched.
- **Worker job-claiming concurrency** (`FOR UPDATE SKIP LOCKED`,
  idempotency keys, dedup keys, retry/backoff) — Phase 7/8/10's design,
  reviewed and left exactly as built; §6's list (dedup, idempotency, lease
  recovery) was already satisfied.
- **Container hardening** — the `backend` Dockerfile's `production` stage
  already runs as a non-root `appuser` with a `HEALTHCHECK`; both
  Dockerfiles already have proper `dev`/`production` multi-stage
  separation.
- **CI already has lint/typecheck/test/build/docker-build stages** — the
  structure was right; §5 below is about what those commands actually
  *checked* once run at their configured, real scope.
- **Structured logging + secret redaction** (`app/core/logging.py`) — a
  denylist filter already strips known-sensitive field names before any
  log line is emitted; request-ID correlation already exists
  (`app/core/middleware.py`).

## 3. A production-safety gap that had zero coverage: startup config validation

Nothing anywhere in this codebase ever checked that `JWT_SECRET`,
`CSRF_SECRET`, `SEED_ADMIN_PASSWORD`, `COOKIE_SECURE`, or `CORS_ORIGINS`
were anything other than their committed, `CHANGE_ME`-prefixed local-dev
defaults before this phase — a deployment that forgot to override even
one of them in production would start up and serve traffic silently
insecure. `app/core/config.py::validate_production_config` is a new,
fail-fast check: a no-op for every `app_env` except `"production"`, where
it raises `ProductionConfigError` (crashing the process before it ever
binds a port) if:

- `jwt_secret`/`csrf_secret`/`seed_admin_password` still equal their
  committed insecure defaults,
- `jwt_secret` is under 32 characters, or equals `csrf_secret`,
- `cookie_secure` is `False`,
- `cors_origins` contains `"*"` or any `localhost`/`127.0.0.1` origin.

Called once, at module import time in `app/main.py`, before
`configure_logging()`/`create_app()` — the earliest possible point, so
`uvicorn app.main:app` fails immediately rather than after binding a
socket. Every error found is aggregated into one message (an operator
fixing one problem and redeploying to discover the next is exactly the
friction this avoids), not raised one at a time. See
`tests/test_production_config_validation.py` for the full set of cases.

`csrf_secret` was discovered, in the course of writing this check, to be
completely unused dead configuration — the actual CSRF mechanism
(`app/auth/middleware.py::CSRFMiddleware`) is a double-submit cookie
comparison (cookie value == header value, both server-set), which needs
no secret at all. It is still included in the insecure-default check
above (cheap, and an operator shouldn't have to know it's inert to know
it's "handled"), but this is flagged here honestly as a documented,
pre-existing, harmless piece of dead config rather than something this
phase wires up or removes (removing a public `Settings` field is a wider
blast-radius change than this phase's scope justifies).

## 4. `/ready` no longer hard-gates on Redis

Given Redis's actual role in this codebase (§2 above — a fail-open
rate-limit cache, never a source of truth), the old `overall_ok = db_ok
and redis_ok` was inconsistent with that design: a Redis outage would
take the *entire API* out of a load balancer's rotation over a dependency
this codebase already treats as best-effort everywhere else. Fixed to
`overall_ok = db_ok` — Redis status is still reported in the
`dependencies` object for operator visibility, it just no longer flips
the HTTP status code. Database has no such fallback anywhere (every
repository requires a session) and remains a hard gate, unchanged. See
`tests/test_readiness_phase15.py` for all four availability combinations.

## 5. CI's actual commands had never been run at their configured, real scope

Every prior phase's own verification narrowly ran `ruff check app [tests]`
and `mypy app --strict` — but CI (`.github/workflows/ci.yml`) has always
run `ruff check .` and `mypy .`, repo-wide. Running those exact commands
for the first time this phase surfaced pre-existing findings, none
introduced by Phase 15:

- **`ruff check .`**: `E402` in `scripts/seed_admin.py`/`scripts/worker.py`
  (the `sys.path.insert(...)`-before-`app.*`-imports pattern both scripts
  need), `E501` in nine lines across `migrations/versions/0005-0008` — the
  exact same lines `docs/security/phase-13-security-review.md` §2 already
  found and explicitly declared "not this phase's scope to fix" — and
  `I001` in `migrations/env.py` (a hand-grouped, phase-commented import
  block that alphabetizing would make less readable, not more correct).
- **`mypy .`**: 14 pre-existing type errors across seven test files
  (`tests/test_worker.py` and every test built on its
  `_SharedSessionFactory` pattern since Phase 7, plus a handful of
  unrelated pre-existing issues in `test_apple_provider.py`/
  `test_compliance.py`/`test_apple_checkin.py`/
  `test_android_enrollment_provisioning.py`) — none introduced by this
  phase, none affecting test correctness (pytest execution is what
  actually verifies test logic; mypy --strict conformance for test files
  was never this project's own convention).

Fixed via `pyproject.toml` configuration only — `[tool.ruff.lint.
per-file-ignores]` (scoped to the exact files/rules above) and extending
`[tool.mypy] exclude` to include `tests/` alongside the pre-existing
`migrations/` exclusion. Zero test files, zero migration files, and zero
scripts were rewritten to satisfy this — per this phase's own "do not
rewrite previous tests, do not modify historical migrations" rules, a
config-level fix is the correct one. `ruff check .` and `mypy .` — CI's
literal commands — both now pass cleanly for the first time in this
project's recorded history.

## 6. Migration chain had never been applied to a real database

`tests/conftest.py` creates schema via `Base.metadata.create_all` against
an in-memory SQLite engine — every phase's test suite has always
bypassed Alembic entirely. The migration chain (`0002` through `0013`)
had therefore never actually been executed against a real Postgres
database anywhere in this project's history.

- **Statically verified locally** (no live Postgres available in this
  dev environment, but this needs none): `alembic history` confirms a
  single, linear, unbranched chain from `<base>` to `0013_phase14_
  applications (head)` — no forks, no gaps, exactly one head.
- **`migrations/env.py`'s model-metadata import list was stale**: Phase
  14's `app.applications.models` was never added to it — found and fixed
  (one new `import` line). This wouldn't have broken `alembic upgrade`
  (0013's migration is hand-written SQL, not autogenerated) but would
  have made a future `alembic revision --autogenerate` silently miss the
  application-management tables.
- **Real `alembic upgrade head` against Postgres**: BLOCKED BY EXTERNAL
  INFRASTRUCTURE — no Docker/Postgres in this dev environment (`docker`,
  `psql`, `pg_ctl` all absent from PATH). Added as a new, permanent CI
  step (`.github/workflows/ci.yml`) that runs `alembic upgrade head`
  against the same ephemeral Postgres service the test job already uses —
  this will be genuinely verified the next time CI runs, which this
  session cannot trigger or observe.

## 7. Worker graceful shutdown

`app/workers/loop.py::run_worker_loop` has accepted an optional
`stop_event: asyncio.Event | None` since Phase 7, but `scripts/worker.py`
never set one — a `docker stop`/orchestrator eviction (`SIGTERM`) hard-
killed the process wherever it happened to be, rather than letting the
current `run_worker_once` cycle finish and exiting at the next poll
boundary. Fixed by wiring `SIGTERM`/`SIGINT` handlers in `scripts/
worker.py` that set the event — `app/workers/loop.py` itself is
unchanged (per the brief's "do not create another worker implementation"
rule). Cooperative shutdown is safe here specifically because
`run_worker_once` already only ever processes one job to full completion
per cycle before the loop re-checks its condition — at most one job's
completion is delayed by a few seconds, never left claimed-but-
unprocessed. On Windows, `SIGTERM` has no `asyncio` signal-handler support
(`NotImplementedError` from `add_signal_handler`) — handled by falling
back to a logged warning; `SIGINT`/Ctrl+C still works there.

## 8. Enrollment endpoint rate limiting

`POST /enrollment/register` is unauthenticated (the enrollment token
itself is the sole authorization, per that router's own long-standing
module docstring) — but unlike `/auth/login`, nothing rate-limited it
before this phase, making it a token-brute-force target with no
throttling at all. `app/auth/rate_limit.py::check_enrollment_rate_limit`
reuses the exact same fixed-window Redis counter design as the pre-
existing `check_ip_rate_limit` (same fail-open-on-Redis-unavailable
behavior, same `login_rate_limit_*` settings rather than adding a
dedicated config knob for an identical "requests per IP per window"
concept) — wired into `app/enrollment/router.py::register_device` as one
new call before the existing registration logic. Device heartbeat
(`POST /devices/{id}/heartbeat`) was deliberately left unthrottled, per
the brief's own explicit instruction (§14: "must tolerate normal fleet
traffic") — heartbeats are frequent, authenticated-by-device-credential,
and not a plausible brute-force target the way a one-shot public
registration endpoint is.

## 9. Security headers

`app/core/security_headers.py::SecurityHeadersMiddleware` (new) adds
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
`Referrer-Policy: strict-origin-when-cross-origin`,
`X-Permitted-Cross-Domain-Policies: none` to every response, and
`Strict-Transport-Security` only when `settings.cookie_secure` is true
(sending HSTS over a plaintext local-dev connection would actively
mislead a browser about a host that isn't HTTPS-only). Deliberately no
`Content-Security-Policy` — this API serves JSON to a separately-deployed
frontend, never HTML a browser renders directly, so a CSP tuned for that
frontend's actual asset origins belongs in the frontend's own deployment
config, not hardcoded into every backend JSON response.

## 10. Worker deployment separation

`docker-compose.yml` gains a `worker` service — same image as `backend`
(no second Dockerfile needed; identical dependencies), running `python
scripts/worker.py` instead of uvicorn, with its own `depends_on`/restart
policy so it scales and restarts independently of API request traffic
(brief §30's "clearly separate API/WORKER/POSTGRES/REDIS/FRONTEND").
`backend/Dockerfile`'s `base` stage now also `COPY`s `scripts/` (it never
did before) — required for the `production` target to be able to run the
worker command at all, since production has no dev volume mounts to
supply it at runtime the way `dev` incidentally could.

## 10.1 Frontend production build never actually received its API URL

Found while writing `docs/operations/production-deployment.md`:
`frontend/Dockerfile`'s `build` stage had no `ARG NEXT_PUBLIC_API_BASE_URL`
declared — a `docker build --build-arg NEXT_PUBLIC_API_BASE_URL=...` call
therefore had no effect (Docker silently ignores a build-arg the
Dockerfile never declared), and Next.js bakes `NEXT_PUBLIC_*` values into
the client bundle at `next build` time from whatever the build environment
actually was. Every production frontend image built with this Dockerfile
would have shipped pointing at `frontend/lib/api/client.ts`'s
`http://localhost:8000` code fallback, not a real production API origin.
Fixed with a two-line `ARG`/`ENV` addition before the build step — a real
bug this phase's documentation-writing process surfaced, not a
speculative one.

## 11. Application deployment feedback (brief §26) — investigated, not fabricated

Re-examined whether Phase 14's Android app-deployment feedback (confirms
the policy-patch API call was accepted, not that the app actually
installed) can be improved with genuinely available signals:

- **Android**: the real completion signal would be a device reporting its
  installed-application state back to this backend. No such channel
  exists — `REQUEST_DEVICE_INFO` (used for `REFRESH_INFO`) returns
  `os_version`/`client_version` only per Google's documented response
  shape (see `docs/android/enterprise-capabilities.md`), and Android
  Management API's Pub/Sub notification types (already integrated in
  Phase 9 for `COMMAND` status updates) do not include a per-app install
  event in this codebase's existing webhook handler
  (`app/platform/android/webhook.py`). Building consumption of a
  currently-unhandled Pub/Sub notification type would be new
  provider-integration surface, not a fix to existing plumbing — and per
  Phase 14's own documented limitation, no device in this system has ever
  completed genuine Android Enterprise enrollment in the first place
  (Phase 5's enrollment is synthetic), so there is no real device that
  could ever emit such an event to test against even if it were built.
  **Conclusion: no change** — Phase 14's existing, honestly-documented
  limitation stands. Fabricating an "INSTALLED-confirmed" status from the
  policy-patch acceptance alone would violate this phase's own explicit
  "do not fabricate installation status" instruction.
- **Apple**: unchanged — no VPP/ABM integration exists (Phase 14's own
  documented boundary), so there is no Apple app-deployment feedback
  channel to investigate improving.

## 12. Device application inventory (brief §27) — investigated, not fabricated

Same investigation for real provider-reported installed-app inventory:
no event type in `app/provider_events/models.py`/`handlers.py` carries an
installed-application list for either platform, and no check-in payload
(Apple's `app/platform/apple/checkin.py`, Android's webhook) reports one
either. **Conclusion: no change** — Phase 14's existing behavior (device
application "inventory" = resolved assignment + this codebase's own
deployment-attempt outcome, never a device-reported install list) remains
the honest one. See `docs/architecture/phase-14.md` §8 for the original
reasoning, unchanged.

## 13. Data retention, backup, and disaster recovery — documented, not implemented

Per brief §21-23: no automatic deletion of any historical table
(`audit_logs`, `security_events`, `device_commands`, `background_jobs`,
`provider_events`, `compliance_results`, `application_deployments`) was
added — retention policy is an operational/compliance decision this
codebase cannot make on an operator's behalf, so recommended periods are
documented (`docs/operations/disaster-recovery.md`) rather than
implemented as a silent auto-delete job. Backup/restore procedure and
full disaster-recovery runbooks are documented in
`docs/operations/disaster-recovery.md` — this environment has no
provisioned Postgres/Redis to actually perform or verify a backup/restore
cycle against, so these are DOCUMENTED procedures, not VERIFIED ones; see
that document's own status markers.

## 14. Known limitations / explicitly out of scope this phase

- Real `alembic upgrade head` against Postgres, real backup/restore, real
  multi-instance worker concurrent-claim testing, real Android/Apple
  device testing — all BLOCKED BY EXTERNAL INFRASTRUCTURE (no Docker, no
  Postgres, no Redis, no real provider credentials/devices in this
  environment), same standing limitation every phase since Phase 6 has
  documented. Not newly introduced by this phase.
- No metrics-emission library was added (brief §10 says "only if it fits
  the existing architecture" and explicitly warns against "a massive
  monitoring platform") — `docs/operations/observability.md` documents
  which metrics matter and how they map onto data already computable from
  existing tables (background_jobs, device_commands, compliance_results)
  via SQL, as a lower-risk starting point than introducing a new
  dependency (prometheus-client or similar) this phase's time budget
  didn't allow properly wiring, testing, and exposing.
- No production `docker-compose.prod.yml` was authored — the existing
  `docker-compose.yml` already has working `dev`/`production` Dockerfile
  targets; `docs/operations/production-deployment.md` documents how an
  operator assembles a production deployment (which target, which
  environment variables, which service topology) without prescribing a
  specific orchestrator (Kubernetes/ECS/plain Compose) this codebase has
  no basis to assume.
- Application deployment feedback and device inventory: investigated,
  unchanged — see §11/§12.
- **No automatic reclaim of a `BackgroundJob` left `CLAIMED` by a worker
  that was killed (not gracefully shut down) mid-job** — a real gap
  surfaced while writing `docs/operations/disaster-recovery.md` §3.1, not
  fixed this phase (would touch Phase 7/8's shared, already-tested job-
  claiming logic beyond this phase's scope of "fix gaps found," not
  open-endedly extend it). Documented with its manual recovery procedure
  there rather than left for an operator to discover mid-incident.
- **Application removal has no `confirm`/`idempotency_key` safeguard**
  unlike WIPE/UNENROLL — genuine gap found during this phase's security
  review, not fixed (see `docs/security/phase-15-security-review.md` §4.1).
