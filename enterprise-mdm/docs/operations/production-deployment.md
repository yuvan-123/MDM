# Production Deployment

How to actually stand this system up in production: prerequisites,
required environment variables, database/migration steps, and how the
API/worker/frontend deploy separately. This document describes the
deployment shape this codebase's existing Dockerfiles/`docker-compose.yml`
already support — it does not prescribe a specific orchestrator
(Kubernetes, ECS, plain VMs) since this codebase has no basis to assume
one. **Nothing in this document has been executed against real
infrastructure in this session** (no Docker/Postgres/Redis available —
see `docs/architecture/phase-15.md`); it is the deployment procedure this
codebase's own configuration implies, not a verified runbook.

## 1. Prerequisites

- PostgreSQL 16+ (matches `docker-compose.yml`'s `postgres:16-alpine`;
  earlier 14/15 likely work too but are untested here).
- Redis 7+ (rate-limiting cache only — see §4 of
  `docs/architecture/phase-15.md`; the system degrades, not fails, without
  it).
- Container runtime for the two provided Dockerfiles (`backend/Dockerfile`,
  `frontend/Dockerfile`), or an equivalent Python 3.12 / Node 20 runtime if
  deploying without containers.
- (Optional, only if using real device management) Android Management API
  service-account credentials + enterprise ID; Apple APNs signing key +
  key ID + team ID + topic. Both are optional — `platform_provider_mode
  =mock` (the default) never requires them and never contacts a real
  provider.

## 2. Required environment variables

Every variable below maps directly to a field in
`backend/app/core/config.py::Settings`. **Never commit real values** —
this table documents names and constraints only.

| Variable | Required in production? | Notes |
|---|---|---|
| `APP_ENV` | Yes | Must be `production` for `validate_production_config` (Phase 15) to enforce everything below. |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://user:pass@host:5432/dbname` — must point at real production Postgres, not the default `mdm:mdm@localhost` dev credential. |
| `REDIS_URL` | Yes (recommended) | Rate limiting degrades gracefully without it (§4 of `docs/architecture/phase-15.md`), but should still be configured in any real deployment. |
| `JWT_SECRET` | Yes | ≥32 random bytes/characters. Startup fails if this is the committed dev default or under 32 characters, or equal to `CSRF_SECRET` (Phase 15). Generate with e.g. `openssl rand -hex 32`. |
| `CSRF_SECRET` | Yes | Currently unused by the CSRF mechanism itself (double-submit cookie — see `docs/security/phase-15-security-review.md` §4.2) but still checked against the insecure default at startup; set a real, distinct value anyway. |
| `COOKIE_SECURE` | Yes — must be `true` | Startup fails otherwise (Phase 15). Requires the API to actually be served over HTTPS. |
| `COOKIE_SAMESITE` | Recommended `lax` or `strict` | `none` requires `COOKIE_SECURE=true` (enforced at startup). |
| `COOKIE_DOMAIN` | If frontend/API are on different subdomains of the same parent domain | Leave unset for same-origin deployments. |
| `CORS_ORIGINS` | Yes | Comma-separated list of the real frontend origin(s), e.g. `https://app.yourorg.example`. Startup fails if it contains `*`, `localhost`, or `127.0.0.1` (Phase 15). |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | Yes, before first run of `scripts/seed_admin.py` | `SEED_ADMIN_PASSWORD` must not be the committed default (enforced at startup). Rotate/change this password immediately after first login — this variable only seeds the *initial* admin account. |
| `PLATFORM_PROVIDER_MODE` | `mock` (default) or `real` | `real` requires the Android/Apple variables below to actually dispatch anything — see each concrete provider's own `PlatformProviderUnavailableError` behavior if they're absent. |
| `ANDROID_SERVICE_ACCOUNT_JSON` / `ANDROID_ENTERPRISE_ID` | Only if using real Android management | The full service-account JSON key as a string, and the bound Android Enterprise's ID. |
| `ANDROID_PUBSUB_VERIFICATION_TOKEN` | Only if using real Android management | Must match the token configured on the Pub/Sub push subscription's endpoint URL (Google's own documented mechanism) — unset means the webhook endpoint rejects every request (fails closed). |
| `APPLE_APNS_KEY_PATH` / `APPLE_APNS_KEY_ID` / `APPLE_APNS_TEAM_ID` / `APPLE_APNS_TOPIC` | Only if using real Apple MDM | `APPLE_APNS_KEY_PATH` must point at a file actually present in the container/host — mount it as a secret file, never bake it into the image. |
| `EMAIL_PROVIDER` | `smtp` in production | `console` (default) only ever logs — fine for local dev, produces no real email delivery in production. Note: this codebase's `smtp` provider option is declared in `Settings` but its implementation was not part of this review's scope to verify. |
| `LOG_LEVEL` | `INFO` (default) or `WARNING` | Avoid `DEBUG` in production — no secret redaction gap is known at any log level (denylist filtering applies uniformly), but `DEBUG` is verbose. |

`NEXT_PUBLIC_API_BASE_URL` (frontend, `frontend/lib/api/client.ts`) must
point at the real, publicly-reachable API origin — baked in at frontend
build time (Next.js `NEXT_PUBLIC_*` convention), not read at runtime, so
it must be set correctly *before* `npm run build`/the frontend Docker
build, not just at container-run time.

## 3. Database setup and migrations

```bash
# 1. Provision Postgres, create the database/user DATABASE_URL points at.
# 2. From backend/, with DATABASE_URL pointed at production:
alembic upgrade head
# 3. Seed the initial platform admin (idempotent — safe to re-run; see
#    scripts/seed_admin.py's own docstring for exact behavior):
python scripts/seed_admin.py
```

The migration chain (`0002` -> `0013`) is statically confirmed linear and
unbranched (`alembic history`) — see `docs/architecture/phase-15.md` §6.
Applying it for real against production Postgres has not been done in
this session (no Postgres available); Phase 15 added a CI step
(`.github/workflows/ci.yml`) that runs `alembic upgrade head` against an
ephemeral Postgres on every push/PR — treat a green CI run as the actual
verification signal before trusting this chain applies cleanly, not this
document's own untested description of it.

## 4. API deployment

Build target: `backend/Dockerfile`'s `production` stage (already
non-root, already has a `HEALTHCHECK` hitting `/health`).

```bash
docker build --target production -t enterprise-mdm-backend:prod ./backend
docker run -d \
  --env-file production.env \
  -p 8000:8000 \
  enterprise-mdm-backend:prod
```

Runs `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2` by
default — tune `--workers` to available CPU (each is a full async event
loop; 2 is this Dockerfile's own conservative default, not a measured
production figure). Scale horizontally by running multiple containers
behind a load balancer — the API is stateless (no server-side session
store; auth is a signed cookie/JWT), so any replica can serve any request.
Point your load balancer's health check at `/health` (liveness — restart
if this fails) and route traffic based on `/ready` (fitness — stop
sending traffic if this returns 503; see `docs/architecture/phase-15.md`
§4 for exactly what gates it).

## 5. Worker deployment

**Must run as its own process/container, separate from the API** — the
API never calls `run_worker_once` itself; only `scripts/worker.py` does.
Same image as the API (no separate Dockerfile — `backend/Dockerfile`'s
`base` stage now `COPY`s `scripts/`, Phase 15), different command:

```bash
docker run -d \
  --env-file production.env \
  enterprise-mdm-backend:prod \
  python scripts/worker.py
```

`docker-compose.yml` now includes a `worker` service demonstrating this
exact shape (dev-target image, for local multi-service testing — swap to
the `production` target the same way `backend`/`frontend` would for a
real deployment). Run **at least one** worker instance; running more than
one is safe (job claiming is `FOR UPDATE SKIP LOCKED`, Phase 7/8 —
unmodified this phase) but concurrent-claim safety under genuine
multi-worker load has never been verified against real Postgres in this
project's history (documented since Phase 7/8; still true). Send `SIGTERM`
(the orchestrator default for a graceful stop) to drain — Phase 15 wired
this to `run_worker_loop`'s existing `stop_event`, so the current job
finishes before the process exits rather than being hard-killed mid-job.

## 6. Frontend deployment

Build target: `frontend/Dockerfile`'s `production` stage.

```bash
docker build --target production \
  --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.yourorg.example \
  -t enterprise-mdm-frontend:prod ./frontend
```

(Phase 15 fix: `frontend/Dockerfile`'s `build` stage previously had no
`ARG NEXT_PUBLIC_API_BASE_URL` declared, so `--build-arg` silently had no
effect — Next.js bakes `NEXT_PUBLIC_*` variables into the client bundle at
`next build` time from its actual build-time environment, and an
undeclared Docker build-arg is ignored rather than erroring, so every
previous production image would have shipped with the frontend still
pointing at its `http://localhost:8000` code fallback regardless of what
was passed. Now declared via `ARG`/`ENV` before the build step — verified
by reading the resulting Dockerfile, not by an actual `docker build` run
in this session, since Docker isn't available here.)

Serves via `npm run start` (Next.js's own production server) on port
3000 — front it with the same load balancer/TLS termination as the API,
or a CDN if serving mostly-static content, per your own infrastructure's
conventions.

## 7. Health checks (for your orchestrator/load balancer config)

- **Liveness**: `GET /health` — process is up, no dependency checks.
  Restart the container if this fails.
- **Readiness**: `GET /ready` — gates on Postgres only (Phase 15); a
  Redis-only outage still reports `ready` (with `redis: unavailable`
  visible in the response body for operator awareness). Stop routing
  traffic here if this returns 503.
- **Provider health**: `GET /providers/status` (requires `AUDIT_READ`
  permission — an authenticated admin call, not a load-balancer probe) —
  poll from your own monitoring, not from the load balancer; a
  provider outage should not remove this instance from rotation (see
  `docs/architecture/phase-15.md` §2).

## 8. Provider credentials

Store `ANDROID_SERVICE_ACCOUNT_JSON`, `APPLE_APNS_KEY_PATH`'s referenced
key file, and every other secret in this table in your platform's actual
secret manager (AWS Secrets Manager, GCP Secret Manager, Kubernetes
Secrets, Vault) — injected as environment variables / mounted files at
container start, never baked into an image layer or committed to source
control. `APPLE_APNS_KEY_PATH` in particular must reference a file path
that exists inside the running container — mount it as a secret volume,
matching how `docker-compose.yml`'s own `env_file: .env` pattern is
already structured for local dev (a real deployment replaces `.env` with
your secret manager's injection mechanism, not a file baked into the
image).

## 9. Rollback

See `docs/operations/disaster-recovery.md` §5 for the full reasoning —
summary: application-code rollback (redeploy previous image) is always
safe; database migration rollback is not, and should never be performed
against a database that has already received writes under the newer
schema. Prefer a new forward migration to undo an unwanted schema change.
