# Disaster Recovery

Backup/restore procedure, failure-mode-by-failure-mode recovery
behavior, and data-retention guidance. **Everything in this document is a
documented procedure, not a verified one** — this development environment
has no Docker, no provisioned Postgres/Redis instance, and no CI run to
actually execute a backup/restore cycle against (see
`docs/architecture/phase-15.md` §6/§13). Where a step has been reasoned
about via code review only, it says so explicitly; nothing here should be
read as "this was tested."

## 1. Backup strategy — DOCUMENTED, NOT VERIFIED

**What must be backed up:**

1. **PostgreSQL** — the only durable, authoritative store in this system.
   Every domain table (`devices`, `device_commands`, `background_jobs`,
   `applications`, `application_deployments`, `audit_logs`, ...) lives
   here. Redis holds nothing that must survive a restart (§4).
2. **Migration state** — the Alembic `alembic_version` table (itself
   inside the Postgres backup above) records which migration the schema
   is currently at; no separate backup mechanism is needed for it beyond
   backing up Postgres itself.
3. **Provider credentials** (`ANDROID_SERVICE_ACCOUNT_JSON`,
   `APPLE_APNS_KEY_PATH` and its referenced key file, etc.) — these live
   in whatever secret manager holds your environment variables (see
   `docs/operations/production-deployment.md` §2), not in the database.
   Back them up the same way you back up any other production secret —
   this document doesn't prescribe a specific secrets-manager backup
   procedure since none is used by this codebase itself.

**Recommended procedure** (standard Postgres practice — not this
codebase's own invention, and not executed in this session):

```bash
# Logical backup (portable across Postgres minor versions, human-inspectable)
pg_dump --format=custom --file=enterprise_mdm_$(date +%Y%m%d_%H%M%S).dump \
  "$DATABASE_URL"

# Encrypt at rest before it leaves the host it was taken on
gpg --encrypt --recipient ops@yourorg.example enterprise_mdm_*.dump
```

- **Frequency**: at minimum, daily full logical backups plus continuous
  WAL archiving (`archive_mode=on`) if your Postgres deployment supports
  point-in-time recovery — this codebase has no opinion on which managed
  Postgres offering you use; most (RDS, Cloud SQL, etc.) provide automated
  snapshot + PITR natively, which is the recommended path over a
  hand-rolled `pg_dump` cron job where available.
- **Encryption**: backups must be encrypted at rest — they contain
  `device_secret_hash`, `hashed_password`, provider-link identifiers, and
  every audit log. Use your storage provider's server-side encryption at
  minimum (S3 SSE, GCS CMEK) plus the `gpg` step above for anything
  leaving that storage.
- **Retention**: recommend 35 days of daily backups + 12 months of
  monthly backups, adjusted to your organization's actual compliance
  requirements (this is a starting recommendation, not a mandated
  value — this codebase has no compliance framework baked in that
  dictates a specific number).
- **Backup verification**: an unverified backup is not a backup — restore
  the most recent backup into a scratch database on a schedule (weekly,
  minimum) and run `alembic current` against it to confirm the schema
  version matches expectations, plus a basic row-count sanity check
  against a handful of tables. **Not automated in this codebase** — no
  scratch-restore job exists; this is a manual/ops-tooling responsibility
  today.

## 2. Restore procedure — DOCUMENTED, NOT VERIFIED

```bash
# 1. Provision a fresh Postgres instance (or point at an existing empty one).
# 2. Restore the logical backup.
pg_restore --clean --if-exists --dbname="$DATABASE_URL" enterprise_mdm_YYYYMMDD.dump

# 3. Confirm the migration chain matches what the restored data expects.
cd backend && alembic current
# If behind the application's expected head (e.g. restoring an older
# backup before rolling forward), apply forward migrations:
alembic upgrade head

# 4. Point the API and worker at the restored database and start them.
# 5. Verify via /ready (must report database: ok) before routing real traffic.
```

**Never roll a migration backward as part of restore** — see §5
(Rollback) for why. If you restored a backup taken *before* a schema
change that has since shipped, the correct step is rolling the restored
data *forward* (`alembic upgrade head`), never editing/downgrading the
currently-deployed schema to match an old backup.

## 3. Disaster scenarios — what recovers automatically vs. needs an operator

| Failure | Automatic recovery | Requires operator action | Data at risk |
|---|---|---|---|
| **Database (Postgres) failure** | None — every request that needs a session fails; `/ready` reports `database: unavailable` and returns 503, so a load balancer stops routing traffic here. | Restore Postgres (managed-service failover, or restore-from-backup per §2). | None if failover is to a replica; up to the backup interval's worth of writes if restoring from a backup with no replica. |
| **Redis failure** | Full — every rate-limit check in `app/auth/rate_limit.py` fails open on any Redis error; `/ready` no longer gates on Redis at all (Phase 15, see `docs/architecture/phase-15.md` §4). Login/enrollment rate limiting is inert while Redis is down, nothing else is affected. | Restart/restore Redis when convenient — no data recovery needed, since nothing durable lives there (rate-limit counters are naturally-expiring, best-effort). | None. |
| **Worker process/container failure** | Partial — `BackgroundJob` rows this worker had `CLAIMED` sit in that state until a future reconciliation mechanism reclaims them. **No such reclaim exists today** (see the finding below) — a worker that dies mid-job leaves that job permanently `CLAIMED`, never retried. | Manually inspect `SELECT * FROM background_jobs WHERE status='CLAIMED' AND claimed_at < now() - interval '10 minutes'` and manually reset qualifying rows to `PENDING` (there is no existing API/CLI for this — a direct SQL `UPDATE`, or a small one-off script using `BackgroundJobRepository`, is the only path today). Restart the worker process/container — orchestrators (Kubernetes, ECS, `restart: unless-stopped` in `docker-compose.yml`) already handle this part automatically. | The specific claimed-but-abandoned job's own action (e.g. one command dispatch) is delayed until manually reclaimed; no data is lost — the row and its payload still exist. |
| **API process/container failure** | Full, if running more than one replica behind a load balancer — stateless (no server-side session, only signed cookies) so any surviving replica serves the next request. Single-replica: full outage until restarted; orchestrator restart policies handle this automatically. | None beyond normal orchestrator restart, assuming multi-replica. | None — no in-flight state lives only in the API process; a request that failed mid-flight simply needs retrying by the client. |
| **Provider outage (Android Management API / APNs unreachable)** | Full, by design — `PlatformProviderUnavailableError` is caught per-command by `CommandService._dispatch` and recorded as a `FAILED` `DeviceCommand` with a clear `error_code`, never an unhandled exception or corrupted state; `app/jobs/retry.py::is_transient` classifies this as retryable, so the `COMMAND_DISPATCH` job itself retries with backoff. `GET /providers/status` reports it independently of `/health`/`/ready` (Phase 9/10/13's own design, reconfirmed unmodified this phase). | None automatic beyond the existing retry — if the outage outlasts `max_attempts`, an admin manually retries the job/command once the provider recovers. | None — every attempt and its failure reason is a permanent row (`device_commands`, `background_jobs`), nothing is silently dropped. |
| **A corrupted/malformed job payload** | Partial — the worker's `except Exception` in `run_worker_once` (`app/workers/loop.py`) catches any processing exception and marks the job `FAILED` with the error captured in `last_error`, so a single bad job cannot crash the loop or block the queue. | Inspect `last_error`, fix the underlying cause (e.g. a bug in a handler), and either manually retry (`POST /jobs/{id}/retry`, Phase 10) or leave it `FAILED` if it's genuinely unrecoverable (e.g. its target device/command was since deleted). | Only that one job's own action; every other queued job is unaffected. |
| **A lost/never-delivered provider event** (e.g. a dropped Pub/Sub push) | None automatic — this codebase has no provider-side "list all events since X" reconciliation pull, only inbound webhook delivery. | Provider-side: Google Cloud Pub/Sub itself retries push delivery per its own subscription's retry policy (external to this codebase) before giving up; if genuinely lost past that, there is no way for this codebase to reconstruct it — the next `REFRESH_INFO`/heartbeat-driven evaluation is the fallback path for state that would have been carried by the missed event. | The specific event's own information (e.g. one command's real-time status update) — mitigated by, not eliminated by, the fact that most state this codebase cares about (command status, compliance) is also independently re-derivable on the next poll/evaluation cycle. |
| **Frontend deployment failure** | None automatic (static/Node.js deployment, no built-in health-based rollback in this codebase's own config) — orchestrator/CDN-level rollback is external to this repo. | Redeploy the previous known-good build. | None — the frontend holds no state of its own; every session lives in cookies/the backend. |

### 3.1 A gap this review surfaced: no automatic reclaim of abandoned `CLAIMED` jobs

`BackgroundJob.status = CLAIMED` has no timeout/lease-expiry mechanism —
if a worker process is killed (not gracefully shut down — see
`docs/architecture/phase-15.md` §7 for the *new* graceful-shutdown path,
which avoids leaving a job `CLAIMED` in the first place by finishing the
current job before exiting) between claiming a job and marking it
completed/failed, that row stays `CLAIMED` forever with no automated
recovery. **Not fixed this phase** — implementing a lease-expiry sweep
(e.g. "reclaim any `CLAIMED` job whose `claimed_at` is older than N
minutes back to `PENDING`") is a real, scoped worker-reliability
improvement, but touches `app/workers/loop.py`'s claiming logic (shared,
tested Phase 7/8 code) and this phase's own instruction was to fix gaps
found, not open-endedly extend already-reviewed worker internals without
a concrete failure this session could reproduce. Documented here as the
honest current behavior and the manual recovery path (§ table above)
rather than silently left for an operator to discover during an actual
incident.

## 4. Redis failure — detailed

Already covered in the table above; expanded here since brief §24 calls
it out specifically. Every Redis touchpoint in this codebase
(`app/auth/rate_limit.py`'s three functions) is wrapped in a bare
`except Exception: return` (or, for the reset helper, `except Exception:
pass`) — a Redis connection error, timeout, or the client being
completely unreachable all degrade to "rate limiting did nothing this
request," never to a 500 or a blocked legitimate request. `/ready`
(Phase 15) reflects this design by not gating on Redis at all — see
`docs/architecture/phase-15.md` §4. There is no cached session, no
cached permission set, and no other Redis-dependent read path anywhere
in this codebase — `get_redis_client()`'s only callers are the three
rate-limit functions and the `/ready` check itself.

## 5. Rollback

**Application code rollback** (redeploy a previous image/build): safe and
ordinary — no different from any other deployment rollback, as long as
the previous version's expected schema is still present (see the
migration-compatibility rule immediately below).

**Database migration rollback**: **never roll back a migration after
application data has been written against the new schema**, per this
phase's own explicit instruction. Every migration in this codebase
(`0002` through `0013`) is additive at the enum level (`ALTER TYPE ...
ADD VALUE` — Postgres has no `DROP VALUE`, so every prior phase's own
`downgrade()` already leaves added enum values in place) and additive at
the table level within a phase (new tables, not destructive column
changes to existing ones) — this was a deliberate design choice across
every phase, not new to Phase 15, and it's exactly what makes
forward-only migration the safe default: a `downgrade()` exists for local
development convenience (undoing an unpushed migration on your own
machine), not as a production incident-response tool. If a deployed
schema change needs to be undone in production, the safe path is a **new
forward migration** that reverses the effect (e.g. drops the column a
previous migration added), never `alembic downgrade` against a database
that has already received real writes under the newer schema — a
downgrade can drop a column/table that already holds real data with no
way back.

**Frontend rollback**: redeploy the previous build artifact — no
database coupling to consider (the frontend is stateless).
