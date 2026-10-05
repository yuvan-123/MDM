# Phase 13 — Policy Violations & Automated Remediation Security Review

## 1. Scope

`PolicyViolation` lifecycle, the `remediation` policy-configuration
section, the `COMPLIANCE_REMEDIATION` background job, and the new
`/compliance/violations*` admin API introduced in Phase 13. Builds on
`docs/security/phase-6-security-review.md` (command safeguards this
phase's remediation dispatches through unchanged) and
`docs/security/phase-12-security-review.md` (capability gating,
provider-command safety) — this review focuses on what's new.

**Code-review-only, same caveat as every prior phase's.** One new
targeted test file (`tests/test_policy_violations_phase13.py`) exists;
every row tied to it is marked PENDING until an actual pytest run
confirms it.

## 2. Static verification actually performed

- `python -c "import app.main"` — succeeds.
- `python -m ruff check app tests migrations/versions/0012_*.py` — all
  checks passed (several line-length violations fixed along the way, in
  `app/compliance/models.py`, `app/compliance/service.py`,
  `app/compliance/router.py`, and the new test file).
- `python -m mypy app --strict` — success, 153 source files.

## 3. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Automated remediation cannot be configured to wipe or unenroll a device** | `RemediationPolicyConfig.command_type` (`app/policies/schemas.py`) is a closed `Literal["REFRESH_INFO", "POLICY_SYNC", "LOCK"]` — WIPE/UNENROLL/REBOOT are not valid values at all; a policy save attempting one gets a 422 from Pydantic validation, not a runtime check that could be bypassed. | `tests/test_policy_violations_phase13.py::test_destructive_remediation_command_type_rejected` — **written, not yet run** | PENDING |
| **A violation cannot be auto-remediated forever** | `_maybe_trigger_remediation` checks `remediation_attempts >= max_attempts` (policy-configured, 1-10) before every trigger and transitions to `REMEDIATION_FAILED` instead, requiring an admin (retry/resolve/disable) to proceed further. | `tests/test_policy_violations_phase13.py::test_violation_full_lifecycle_trigger_exhaust_retry_resolve` (max_attempts=1, reaches REMEDIATION_FAILED after exactly one exhausted attempt) — **written, not yet run** | PENDING |
| **Automated remediation never bypasses the existing command security pipeline** | The `COMPLIANCE_REMEDIATION` job creates its `DeviceCommand` through the **unmodified** `CommandService.create_command` — the same capability gate (Phase 12), tenant-scoped device lookup, and idempotency-key mechanism every admin-issued command already goes through. No parallel dispatch path was added. | Code review (`app/workers/loop.py::_process_compliance_remediation` calls `CommandService.create_command` directly, no bypass) | PASS |
| **A command reaching COMPLETED never by itself resolves a violation** | `_reconcile_violations` only resolves a violation via the rule genuinely no longer being in the current evaluation's failing set (`resolution_reason="RULE_PASSED"`) — a `REMEDIATING` violation whose command reaches any terminal state (including COMPLETED) only reopens to OPEN if the rule still fails; it is never marked RESOLVED by the command status alone. | `tests/test_policy_violations_phase13.py::test_violation_full_lifecycle_trigger_exhaust_retry_resolve` (asserts REMEDIATION_FAILED, not RESOLVED, after a COMPLETED command with the device still reporting non-compliant) — **written, not yet run** | PENDING |
| **Repeated evaluation cannot create duplicate violations or duplicate remediation commands** | `PolicyViolation`'s unique constraint is `(organization_id, device_id, policy_id, rule_code)`, checked via `get_by_device_policy_rule` before insert; `COMPLIANCE_REMEDIATION` jobs are deduplicated by `f"compliance_remediation:{violation.id}"` via the existing `JobService._enqueue` dedup mechanism (unchanged from Phase 7); the resulting `DeviceCommand` itself carries `idempotency_key=f"remediation:{violation.id}:{attempt}"`, the existing Phase 6 command-idempotency mechanism. Three independent layers, all pre-existing infrastructure. | `tests/test_policy_violations_phase13.py::test_repeated_evaluation_does_not_duplicate_violation` — **written, not yet run** | PENDING |
| **Violation/remediation-management actions are gated by a permission distinct from ordinary read/evaluate** | New `compliance.remediation.manage` permission required for retry/resolve/disable/enable — confirmed absent from `HELP_DESK`'s and `AUDITOR`'s role definitions by direct inspection of `app/rbac/permissions.py`; granted only to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN`. | `tests/test_policy_violations_phase13.py::test_help_desk_cannot_manage_remediation` — **written, not yet run** | PENDING |
| **Cross-organization violation access is impossible** | `get_violation`/`list_violations` resolve through `PolicyViolationRepository`'s org-scoped queries (every method takes `organization_id` and filters on it, no bare lookup) — a violation from a different organization 404s identically to a nonexistent one. | `tests/test_policy_violations_phase13.py::test_org_b_cannot_see_org_a_violations` — **written, not yet run** | PENDING |
| **Manual remediation retry cannot be used to bypass the policy's current remediation configuration** | `retry_remediation` re-fetches the policy's **current** active version's `remediation` config (`_current_remediation_config`) rather than reusing whatever was true when the violation last failed — if an admin changed the policy to `mode=NONE` in the meantime, retry correctly rejects with `REMEDIATION_NOT_CONFIGURED` rather than silently using stale settings. | Code review (`app/compliance/service.py::retry_remediation`) — no dedicated test drives this specific "policy config changed between failure and retry" scenario | PASS (reasoning only) |
| **A violation can be excluded from automation without touching the policy** | `automated_remediation_disabled` is a per-violation flag, not a policy-level change — disabling remediation for one problematic device/rule never silently disables it for every other device the same policy applies to. Deliberately **not reset** on a regression-reopen, so an admin's earlier choice survives the same problem recurring. | Code review (`app/compliance/models.py::PolicyViolation`, `_reconcile_violations`'s reopen path explicitly omits `automated_remediation_disabled` from its update fields) | PASS |
| **Audit logging is bounded, not a per-evaluation flood** | Violation detection/update is never audited (would fire every heartbeat for a persistently non-compliant device); only `COMPLIANCE_REMEDIATION_TRIGGERED` (bounded by `max_attempts`), manual retry/resolve/disable/enable are. | Code review (`app/compliance/service.py`, `app/workers/loop.py::_process_compliance_remediation`) | PASS |
| **The new system-initiated command path (`actor_user_id=None`) cannot be triggered by anything other than the compliance remediation job** | `CommandService.create_command`'s widened `uuid.UUID \| None` type is used with `None` from exactly one call site (`_process_compliance_remediation`); every admin-facing router (`app/commands/router.py`) still passes `current_user.user_id`, unchanged. | Code review (grep-equivalent: only one `actor_user_id=None` call site in `app/`) | PASS |

## 4. Discovered & Resolved Issues (during this phase's implementation)

1. **A real design conflict caught before writing code, not after**: the
   natural place to look up "which command is remediating this
   violation" (`command.provider_name`) is NOT the same value as the
   violation's own provider identity in every case — but this was
   correctly avoided by keying the remediation-command lookup on
   `last_remediation_command_id` directly (an explicit FK) rather than
   re-deriving it from `provider_name`, sidestepping the exact class of
   bug Phase 12's UNENROLL cleanup hit (see
   `docs/security/phase-12-security-review.md` §4.4) before it could
   recur here.
2. **Where to audit "remediation triggered"** — initially logged at both
   the decision point (`_maybe_trigger_remediation`, when the job is
   enqueued) and at actual command creation (the job handler), which
   would have double-audited every remediation attempt. Resolved by
   auditing only once, at actual command creation, matching how
   `DEVICE_COMMAND_CREATED` is audited at creation rather than at an
   earlier "decided to create one" point.
3. **A stray call to a nonexistent helper function** was written and
   caught during self-review (not by a test run) while drafting the job
   handler's exception-handling branch — simplified to let
   `_maybe_trigger_remediation`'s existing `attempts >= max_attempts`
   check own that decision on the device's next evaluation, rather than
   duplicating attempt-limit logic inside the job handler.
4. Several `E501` line-length violations across
   `app/compliance/{models,service,router}.py` and the new test file —
   caught by ruff, reformatted.

## 5. Known Gaps / Deferred

- **No pytest run has happened for the new Phase 13 test file** — every
  PENDING row above reflects implemented-and-reviewed behavior, not
  confirmed behavior. Top-priority gap to close, especially given this
  phase's test drives the longest multi-job chain of any phase so far
  (evaluation -> remediation -> dispatch -> re-evaluation -> exhaustion
  -> manual retry -> re-evaluation -> resolution, all through
  `worker_loop.run_worker_once`).
- **No dedicated test for the "policy config changed between violation
  creation and manual retry" scenario** (§3, `retry_remediation` row) —
  covered by code review only.
- **No real NOTIFY delivery mechanism** — documented in
  `docs/architecture/phase-13.md` §10 as an intentional scope boundary,
  not a security gap: NOTIFY mode never claims to send anything it
  doesn't.
- **No bulk violation-remediation admin controls** — per-violation only
  this phase; documented as deferred scope in
  `docs/architecture/phase-13.md` §10.
- Every other gap already documented in
  `docs/security/phase-6-security-review.md` §4 and
  `docs/security/phase-12-security-review.md` §5 (no live Postgres, no
  real credentials, no Docker, no real ADE) is unchanged and still
  applies — remediation commands flow through the exact same
  Mock/Android/Apple provider paths as every admin-issued command,
  never a new dispatch mechanism.
