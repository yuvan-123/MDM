# Observability

How to see what this system is doing in production: logs, correlation
IDs, metrics, and a troubleshooting flow for the most common questions an
operator asks. Status markers below distinguish IMPLEMENTED (exists in
code today), DOCUMENTED (a recommended approach, not yet wired up), and
BLOCKED BY EXTERNAL INFRASTRUCTURE (needs a real deployment to verify).

## 1. Logs — IMPLEMENTED

Every log line is a single JSON object (`app/core/logging.py`,
structlog), written to stdout. Fields present on every line:
`timestamp`, `level`, `event` (the log's own short name, e.g.
`http_request`, `worker_job_completed`), `service` (always
`enterprise-mdm-backend`).

**Never logged, by design** (`app/core/logging.py::_SENSITIVE_KEYS`,
enforced as a denylist filter on every event dict before it's rendered,
regardless of call site): `password`, `hashed_password`, `access_token`,
`refresh_token`, `authorization`, `jwt_secret`, `api_key`, `secret`,
`client_secret`, `private_key`. If a new call site ever binds one of
these key names, the value is replaced with `***REDACTED***` — this is a
key-name denylist, not a value-pattern scanner, so a genuinely new kind of
secret must be bound under one of these names (or an equivalent added to
the set) to get this protection; it does not scan free-text log messages
for secret-*looking* strings.

Collect stdout from every container (API, worker) with whatever your
platform provides — CloudWatch Logs, GCP Cloud Logging, a Loki/Fluent Bit
sidecar, `docker logs`/`kubectl logs` piped to a log shipper. No
application-side log-file rotation exists or is needed — containers
should never write logs to a local file.

## 2. Correlation — IMPLEMENTED (request), DOCUMENTED (cross-entity)

**Request ID** (`app/core/middleware.py::RequestContextMiddleware`):
every request gets an `X-Request-ID` (client-supplied if present,
generated otherwise), echoed back as a response header and included in
every log line for that request (`http_request`, and every `AppError`/
validation/unhandled-exception response via `app/core/errors.py`'s
`_envelope`). This is the starting point for any "what happened for this
one request" investigation — grep logs for the `request_id` from a
client-reported error.

**Cross-entity correlation** (device -> command -> job -> provider event)
is DOCUMENTED, not a dedicated trace-ID field threaded through every log
line — this codebase's actual mechanism is the *data model itself*, which
already carries these links as real foreign keys, so the correlation path
is a lookup chain, not a log search:

```
device_id
  -> GET /devices/{id}/commands            (every command issued to it)
  -> GET /devices/{id}/commands/{id}       (one command's own result/error)
       command.provider_command_id         (the provider's own tracking id,
                                             where the provider assigns one)
  -> GET /jobs?dedup_key=command_dispatch:{command_id}   (Phase 10 traceability —
       app/jobs/service.py::list_jobs's command_id filter translates to
       this exact dedup_key convention)
  -> GET /provider-events?...              (inbound events, org-scoped)
```

Application deployment traceability (Phase 14) follows the identical
shape: `GET /applications/{id}/deployments` -> `ApplicationDeployment.
last_command_id` -> `GET /devices/{id}/commands/{command_id}`.

Compliance/remediation traceability (Phase 13): `GET /compliance/
violations?device_id=...` -> `PolicyViolation.last_remediation_command_id`
-> the same command lookup.

**Recommendation for a future phase**, not implemented here: bind
`request_id` into `structlog.contextvars` for the duration of a request
(already partially possible via `structlog.contextvars.merge_contextvars`,
already in the processor chain) so *every* log line emitted while
handling a request — not just the final `http_request` summary line —
carries it automatically, including from deep inside a service call.
Today, only the summary line and error-envelope responses carry
`request_id`; an `AuditRepository.log_event` call or a mid-request
`logger.info(...)` elsewhere does not unless the caller explicitly passes
it.

## 3. Job / command / provider-event IDs — IMPLEMENTED

- `job_id`, `job_type` on every `worker_job_completed`/`worker_job_failed`
  log line (`app/workers/loop.py`).
- `command_id`, `command_type` on every `DEVICE_COMMAND_*` audit event
  (`app/audit/models.py`) and Phase 6 command-lifecycle log line.
- `violation_id`, `device_id`, `command_id` on every
  `COMPLIANCE_REMEDIATION_*` audit event.
- `application_id`, `device_id`, `deployment_id`, `command_id` on every
  `APPLICATION_*` audit event (Phase 14).

All of the above are queryable via `GET /audit-logs` (existing admin API,
`AUDIT_READ` permission) in addition to structured stdout logs — the
audit log is the durable, queryable record; stdout logs are for live
tailing/alerting.

## 4. Metrics — DOCUMENTED, not implemented

No metrics-emission library (`prometheus-client`, StatsD, OpenTelemetry
Metrics) exists in this codebase today. Per this phase's own instruction
("only if it fits the existing architecture... do NOT introduce a massive
monitoring platform"), none was added — introducing and correctly wiring
a new dependency, its own `/metrics` endpoint, its own security
considerations (should it require auth? be on a separate internal port?),
and tests for all of that is a properly-scoped follow-up task, not
something to bolt on inside this phase's budget.

What's useful to track, and how to compute each **today**, without new
instrumentation — every one of these is a SQL query against existing
tables, runnable ad hoc or wired into whatever metrics/BI tool an operator
already has pointed at the production database:

| Metric | Existing source |
|---|---|
| HTTP request failures / latency | `RequestContextMiddleware`'s `http_request` log line already carries `status_code`/`duration_ms` for every request — aggregate from log lines via your log platform's own query language (e.g. a CloudWatch Logs Insights query, a Loki LogQL query) rather than a new counter. |
| Background jobs pending / failed | `SELECT job_type, status, count(*) FROM background_jobs GROUP BY job_type, status` (`app/jobs/models.py`). |
| Command failures by type/provider | `SELECT command_type, provider_name, status, count(*) FROM device_commands GROUP BY 1,2,3`. |
| Provider failures | `GET /providers/status` (already exists, Phase 10) — poll it on an interval from any external monitor; it never touches a real device (see `app/platform/health.py`'s own docstring). |
| Device check-in volume | `SELECT count(*) FROM devices WHERE last_seen_at > now() - interval '1 hour'` (or bucket by hour for a trend). |
| Compliance failures | `SELECT status, count(*) FROM device_compliance GROUP BY status` (already exposed via `GET /compliance/summary`). |
| Application deployment failures | `SELECT status, count(*) FROM application_deployments GROUP BY status`. |

**Recommended alert thresholds** (tune per fleet size — these are
starting points, not tested against real production traffic):
- `background_jobs` in `FAILED` status growing without a corresponding
  admin retry within, say, 30 minutes — indicates a systemic dispatch
  problem, not an isolated transient failure.
- `GET /ready` returning 503 for more than one consecutive health-check
  interval — Postgres is the hard gate (§4 of `docs/architecture/
  phase-15.md`), so this means the database is actually unreachable.
- `GET /providers/status` reporting `configured: true, ok: false` for a
  provider that was previously healthy — a real credential/connectivity
  regression, not a "provider not configured in this environment" no-op.

## 5. Troubleshooting flow

**"A specific admin action failed"** — get the `request_id` from the
error response body (`error.request_id`) or the `X-Request-ID` response
header, grep logs for it.

**"A device isn't receiving a command"** — `GET /devices/{id}/commands`
-> find the command -> check its `status`/`error_code`/`error_message` ->
if `SENT` and stuck, check `GET /providers/status` for that platform ->
if the command shows `PENDING` for longer than expected, check
`GET /jobs?job_type=COMMAND_DISPATCH` for a `FAILED` job with a matching
dedup key and inspect `last_error`.

**"Compliance says a device is NON_COMPLIANT and I don't know why"** —
`GET /compliance/devices/{id}` -> `details` array has one entry per rule
with `code`/`outcome`/`message`/`expected`/`actual` — this is the direct
answer, no correlation needed.

**"An application deployment seems stuck"** — `GET /applications/{id}/
deployments?device_id=...` -> check `status`/`last_error` -> if
`INSTALLING` for longer than expected, follow `last_command_id` to
`GET /devices/{id}/commands/{command_id}` for the real provider-side
error.

**"Is the worker even running?"** — `worker_starting/worker_stopped/
worker_shutdown_requested` log lines (Phase 15, `scripts/worker.py`);
absence of any `worker_job_completed`/`worker_reconciled_expired_commands`
lines for an extended period with `background_jobs` accumulating in
`PENDING` is the operational signal the worker process itself is down or
stuck — check the container/process directly (BLOCKED BY EXTERNAL
INFRASTRUCTURE to verify further here — no running worker to observe in
this dev environment).
