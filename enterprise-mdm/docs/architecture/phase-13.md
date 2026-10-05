# Phase 13 — Advanced Policy Enforcement + Automated Remediation

Phases 5-12 built a complete evaluate-and-record compliance pipeline
(`Policy -> PolicyAssignment -> ComplianceService.evaluate_device ->
DeviceCompliance/ComplianceResult`) but stopped at recording *that* a
device was non-compliant — nothing tracked *which specific rule* had been
failing across evaluations, nothing decided whether to act on it, and
nothing could safely turn "still failing" into a bounded, auditable
remediation attempt. Phase 13 closes that gap by extending the existing
pipeline with one new concept — `PolicyViolation` — and one new decision
point inside the same `evaluate_device` call every trigger already goes
through.

It does not replace the compliance engine, the rule registry, the policy
resolution/precedence logic, `DeviceCommand`, or the background-job
worker. Every one of those is reused exactly as Phases 5-12 left it.

**Verification status**: `python -c "import app.main"` succeeds, ruff and
`mypy --strict` are clean against every file this phase touches. See
`docs/security/phase-13-security-review.md` §4 for exact commands and
current test-run status.

## 1. Why policy conflicts were already a solved problem

Phase 13's brief asks how the system should behave when multiple
policies could apply to one device. It already can't: `PolicyResolutionService.resolve_for_device`
(`app/policies/service.py`, Phase 5) walks precedence
`DEVICE > DEVICE_GROUP > USER > ORGANIZATION` and returns **at most one**
`(Policy, PolicyVersion)` — the first ACTIVE policy found at the
highest-precedence scope that has one, tie-broken by most-recent
assignment within a level. Every violation this phase creates is
therefore always attributable to exactly one policy; "policy A says X,
policy B says Y" cannot arise structurally. Nothing was added to solve
this — it's documented here so a future phase doesn't have to
rediscover it.

## 2. `PolicyViolation` — the new model

`app/compliance/models.py::PolicyViolation`, one row per
`(organization, device, policy, rule_code)` that has *ever* failed —
deliberately a new table, not squeezed into `DeviceCompliance` (a
per-device cache, overwritten every evaluation) or `ComplianceResult`
(pure insert-only history). Neither of those attaches "this is still the
same open problem" identity to one specific rule failure across many
evaluations; `PolicyViolation` is that identity.

Lifecycle (`ViolationStatus`):

```
OPEN -> REMEDIATION_PENDING -> REMEDIATING -> OPEN (retry) or RESOLVED or REMEDIATION_FAILED
```

- **OPEN**: detected, not (yet) being auto-remediated.
- **REMEDIATION_PENDING**: a `COMPLIANCE_REMEDIATION` job has been
  enqueued (see §4).
- **REMEDIATING**: the job created a `DeviceCommand`; waiting to see
  whether it reaches a terminal state.
- **RESOLVED**: the rule now passes, or the policy/assignment that
  produced this violation no longer applies (§6). Not always terminal —
  a later regression reopens the *same* row fresh (new `detected_at`,
  `remediation_attempts` reset to 0) rather than creating a duplicate.
- **REMEDIATION_FAILED**: `remediation_attempts` reached the policy's
  configured `max_attempts` with no successful resolution. Requires an
  admin: manual retry, resolve, or disable automated remediation (§8).

Idempotent creation (brief §4): uniqueness is
`(organization_id, device_id, policy_id, rule_code)`, not filtered to
open rows — a repeated evaluation that still finds the same rule failing
*updates* the existing row (`last_seen_at`, `message`,
`expected_value`/`current_value`), never inserts a second one. The
database's own unique constraint (`uq_policy_violation_device_policy_rule`)
is the structural backstop.

`RuleResult` (`app/compliance/rules.py`) gained optional `expected`/
`actual` fields, populated by all five existing rule functions — the
minimum extension needed for `PolicyViolation.expected_value`/
`current_value` to be genuinely structured data instead of only a free-text
message. `DeviceCompliance.details`/`ComplianceResult.details` (the JSON
snapshot) now include these two fields too, for consistency.

## 3. Remediation configuration

New `RemediationPolicyConfig` (`app/policies/schemas.py`), a section of
the existing `PolicyConfiguration` JSON blob — no migration needed for
this part, matching Phase 5's original design intent for that column.

```json
"remediation": { "mode": "COMMAND", "command_type": "LOCK", "max_attempts": 3 }
```

- `mode`: `NONE` (default) / `NOTIFY` / `COMMAND`.
- `command_type`: a **closed allowlist** — `REFRESH_INFO`, `POLICY_SYNC`,
  or `LOCK` only. `WIPE`, `UNENROLL`, and `REBOOT` are structurally
  impossible to configure here — a 422 at policy-save time, not a
  runtime safeguard someone could get wrong later. This is what "prefer
  NOT implementing automatic destructive remediation" (brief §7/§30)
  means in this codebase: not a warning, a type the schema cannot
  express. REBOOT is excluded too — disruptive to an in-use device and
  not among the brief's own safe examples (refresh, compliance check,
  lock, sync).
- `max_attempts`: 1-10, default 3 — the loop-prevention bound (§7 below).
- A `model_validator` enforces `command_type` is set if and only if
  `mode == "COMMAND"`.

`NOTIFY` mode currently means exactly what it says and no more: the
violation is created/tracked normally (queryable via the admin API,
§9), but no command is ever issued. This codebase has no notification
channel (email, webhook, etc.) to actually send through — building one
was out of scope; "NOTIFY" is honestly a "flagged, not auto-remediated"
mode today, not a promise of an outbound message.

## 4. Remediation trigger and the `COMPLIANCE_REMEDIATION` job

`ComplianceService._reconcile_violations` runs at the end of every
`evaluate_device` call — the same single choke point every trigger
(heartbeat, manual `POST /compliance/evaluate/{id}`, `POLICY_REEVALUATION_FANOUT`)
already goes through, so no second evaluation path was introduced (brief
§5/§18).

For each rule still failing under the resolved policy, if the violation
transitions to (or remains) `OPEN` and `remediation.mode == "COMMAND"`
and remediation isn't disabled for that specific violation:

- If `remediation_attempts >= max_attempts`: `REMEDIATION_FAILED`, stop.
- Otherwise: increment `remediation_attempts`, set
  `REMEDIATION_PENDING`, and enqueue a new job type,
  `JobType.COMPLIANCE_REMEDIATION` (`app/jobs/models.py`), with
  `command_type` **snapshotted** into the payload at this moment —
  deliberately not re-read from the policy when the job runs, mirroring
  `DeviceCommand.provider_name`'s own "recorded at dispatch time, even
  if config later changes" precedent. `dedup_key` is the violation's own
  id — the same one-active-job-per-target invariant
  `enqueue_command_dispatch` already establishes for commands, giving
  remediation idempotency (brief §9) for free from existing
  infrastructure.

`app/workers/loop.py::_process_compliance_remediation` (the worker's
handler for this job type) creates the `DeviceCommand` via the
**existing, unmodified** `CommandService.create_command` — no second
command table, no provider-specific logic in the compliance engine
(brief §8/§33: `Policy -> Remediation decision -> CommandService ->
Provider capability -> Provider implementation`, exactly as specified).
`idempotency_key` is `f"remediation:{violation.id}:{violation.remediation_attempts}"`,
so a worker crash mid-job can never double-dispatch. On success, the
violation moves to `REMEDIATING` and records
`last_remediation_command_id`; `COMPLIANCE_REMEDIATION_TRIGGERED` is
audited once, here, at actual command creation — matching how
`DEVICE_COMMAND_CREATED` is audited at creation, not at the earlier
"decided to create one" point.

`actor_user_id` on `CommandService.create_command` is now
`uuid.UUID | None` — `DeviceCommand.requested_by` has been nullable and
documented as "reserved for a future system-initiated command" since
Phase 6; this is that future. Every admin-facing router still always
passes a real user id; only this one system-triggered call site passes
`None`.

If `create_command` itself raises synchronously (`DEVICE_NOT_ENROLLED`,
`COMMAND_TYPE_NOT_SUPPORTED` — e.g. a policy configured `LOCK` for a
platform whose current provider doesn't support it), the job catches it,
logs a warning, and reopens the violation to `OPEN` rather than deciding
`REMEDIATION_FAILED` itself — `_maybe_trigger_remediation`'s own
`attempts >= max_attempts` check, re-run on the device's next evaluation
(against whatever the *current* policy config is by then), owns that
decision in exactly one place.

## 5. Reusing existing infrastructure for retries, backoff, and offline devices

Brief §10/§11/§14 ask for retry classification, backoff, and offline-device
handling. All three fall out of mechanisms Phases 6-8 already built,
with **no new code**:

- **Provider unavailable / network timeout / command rejected**: these
  are exactly the failure modes `CommandService._dispatch` already
  classifies and records on the `DeviceCommand` itself (`FAILED` with an
  `error_code`) via its existing, unmodified try/except. Phase 13 never
  re-implements this classification — it only *reads* the resulting
  command status.
- **Device offline**: a command a device never picks up sits `SENT`
  until Phase 6/7's existing expiration + reconciliation
  (`device_command_expire_hours`, `reconcile_expired_sent_commands`)
  marks it `EXPIRED`. No offline-detection code was added for this
  phase — an EXPIRED command is just one more terminal, non-COMPLETED
  status the reconciliation step below already treats uniformly.
- **Backoff between attempts**: the heartbeat interval itself is the
  throttle — a new remediation attempt is only ever considered on the
  *next* evaluation, which only happens on the next heartbeat (or manual
  trigger). No artificial backoff timer was added; one wasn't needed.

## 6. Post-remediation reevaluation — never assume success from a command

Brief §13's central rule: a command reaching `COMPLETED` never by itself
implies the policy is satisfied. On every evaluation,
`_reconcile_violations` checks every currently-active violation:

- If the resolved policy no longer matches (`policy_id` differs from
  what's stored — including "no policy resolves at all") -> `RESOLVED`,
  `resolution_reason` = `"POLICY_UNASSIGNED"` (nothing resolves) or
  `"POLICY_CHANGED"` (a different one now does) — evaluate_device can't
  distinguish "explicitly unassigned" from "a higher-precedence policy
  now wins" more precisely than that from inside itself; documented as a
  deliberate heuristic, not a gap.
- Else if the rule is no longer in this evaluation's failing set ->
  `RESOLVED`, `resolution_reason = "RULE_PASSED"` — **only this path**,
  driven by the rule actually re-passing, ever resolves a violation
  automatically. A `REMEDIATING` violation whose command reached
  `COMPLETED` does **not** get resolved by that fact alone.
- Else (still failing, under the same policy): if the violation was
  `REMEDIATING` and its `last_remediation_command_id` has reached *any*
  terminal state (`COMPLETED`, `FAILED`, `CANCELLED`, `EXPIRED`), it
  reopens to `OPEN` — eligible for another attempt, subject to
  §4's attempt-limit check. Deliberately narrower than `WIPE`'s own
  proactive credential-clearing (which triggers on `SENT` too, Phase 6):
  reopening a violation for another remediation round is safe to do
  conservatively, only once the previous attempt has genuinely finished
  one way or another.

## 7. Admin API

New, under the existing `/compliance` prefix (`app/compliance/router.py`)
— no separate violation-management architecture:

```
GET  /compliance/violations                  compliance.read (?device_id=&policy_id=&status=)
GET  /compliance/violations/{violation_id}   compliance.read
POST /compliance/violations/{violation_id}/retry                 compliance.remediation.manage
POST /compliance/violations/{violation_id}/resolve                compliance.remediation.manage
POST /compliance/violations/{violation_id}/disable-remediation    compliance.remediation.manage
POST /compliance/violations/{violation_id}/enable-remediation     compliance.remediation.manage
```

New permission `compliance.remediation.manage` bundles all four
mutating actions (deliberately distinct from `compliance.evaluate`,
which triggers an ordinary evaluation, not an explicit intervention on
an already-detected problem) — granted to `ORGANIZATION_ADMIN`/
`SECURITY_ADMIN` only, matching every other high-risk manage-style
permission in this codebase.

- **Retry** (`retry_remediation`): only from `REMEDIATION_FAILED`.
  Deliberately **resets** `remediation_attempts` to 0 — unlike Phase
  10's job retry (which preserves `attempts`/`last_error` specifically
  to stop an *automatic* retry loop from becoming unbounded), a manual
  retry here is itself the human judgment call that a fresh count is
  warranted, not a mechanism that could loop on its own.
- **Resolve** (`resolve_violation`): an admin asserting the problem is
  handled (fixed outside this system, or accepted as a known exception).
  Does not touch `DeviceCompliance`/`ComplianceResult` — if the rule
  genuinely still fails, the next evaluation reopens this exact
  violation fresh, it is not permanently suppressed.
- **Disable/enable remediation** (`set_remediation_disabled`): an
  admin override on *this specific violation* — the policy's own
  `remediation` configuration is untouched, so other devices/violations
  under the same policy are unaffected. Deliberately survives a
  regression-reopen (§2) — an admin's earlier choice isn't silently
  reversed by the same problem recurring.

## 8. Audit logging — bounded, not per-evaluation

Per brief §25 ("do not generate excessive audit events for every
evaluation cycle"), violation detection/update itself is **never**
audited — that would fire on every heartbeat for a persistently
non-compliant device. Only bounded, state-changing actions are:
`COMPLIANCE_REMEDIATION_TRIGGERED` (at most `max_attempts` times per
violation, ever), `COMPLIANCE_REMEDIATION_RETRIED`,
`COMPLIANCE_REMEDIATION_DISABLED`/`_ENABLED`, and
`COMPLIANCE_VIOLATION_RESOLVED` (manual resolve only — automatic
`RULE_PASSED`/`POLICY_UNASSIGNED`/`POLICY_CHANGED` resolutions are not
audited, for the same "not every evaluation cycle" reason).

## 9. Database migration

`0012_phase13_policy_violations.py` — additive only, does not touch
0001-0011:
- New `policy_violations` table (see §2's column list in
  `app/compliance/models.py`).
- New `policy_violation_status` Postgres enum.
- `ALTER TYPE audit_event_type ADD VALUE` x5 (the new audit events, §8).
- `ALTER TYPE background_job_type ADD VALUE 'COMPLIANCE_REMEDIATION'`.

No changes to `device_commands`, `background_jobs`, `policies`, or
`policy_versions` — remediation reuses `DeviceCommand` and
`BackgroundJob` entirely unchanged, and the new `remediation`
configuration section lives inside `PolicyVersion.configuration`'s
existing JSON column.

## 10. What's genuinely done vs. deferred

**Done:** violation model + idempotent lifecycle, remediation
configuration with a structurally-enforced non-destructive allowlist,
trigger/attempt-limit/retry-via-existing-job-infra, post-remediation
reevaluation that never assumes success from a command alone,
policy-unassignment/change resolution, admin API (list/detail/retry/
resolve/disable/enable), bounded audit logging, RBAC, tenant isolation.

**Deliberately deferred, not attempted half-built:**
- **A real NOTIFY delivery channel** (email/webhook/etc.) — this
  codebase has none; `NOTIFY` mode is honest about tracking-without-acting
  rather than fabricating a notification that goes nowhere.
- **Bulk/group violation remediation controls** (e.g. "retry all failed
  violations for this policy") — the per-violation admin API is the
  full extent of this phase; a bulk variant would need its own safety
  review this pass didn't scope for.
- **A dedicated backoff timer between remediation attempts** — evaluated
  and judged unnecessary: the heartbeat cadence itself already throttles
  attempts (§5); adding a second timer would be redundant complexity.
