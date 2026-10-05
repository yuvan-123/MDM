# Phase 14 — Enterprise Application Management Security Review

## 1. Scope

Application catalog, assignment, deployment (`INSTALL_APPLICATION`/
`UNINSTALL_APPLICATION` DeviceCommand types), device application
inventory, and required-application compliance integration introduced in
Phase 14. Builds on `docs/security/phase-6-security-review.md` (command
dispatch safeguards this phase's deployment flows through unchanged),
`docs/security/phase-12-security-review.md` (capability gating), and
`docs/security/phase-13-security-review.md` (compliance/remediation
integration patterns) — this review focuses on what's new.

**Code-review-only, same caveat as every prior phase's.** One new
targeted test file (`tests/test_applications_phase14.py`) exists; every
row tied to it is marked PENDING until an actual pytest run confirms it.

## 2. Static verification actually performed

- `python -c "import app.main"` — succeeds.
- `python -m ruff check app migrations/versions/0013_phase14_applications.py`
  — all checks passed.
- `python -m mypy app --strict` — success, 161 source files.
- `python -m ruff check tests/test_applications_phase14.py` — all checks
  passed.
- Frontend: `npx tsc --noEmit`, `npx eslint` on every changed/new file, and
  `npm run build` (full production build; `/applications` and
  `/applications/[id]` routes confirmed in output) — all clean.
- **Targeted pytest has NOT been run** — compile-checked and ruff-clean,
  logic unverified by an actual test run.

## 3. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **An unsupported provider capability cannot be silently accepted** | `APPLICATION_INSTALL`/`APPLICATION_REMOVE` are gated by `CommandService`'s pre-existing `_CAPABILITY_BY_TYPE` + `_require_capability_supported` check (Phase 12) — identical mechanism every other command type uses, no bypass added. Apple's provider deliberately omits both from `_SUPPORTED_CAPABILITIES`. | `tests/test_applications_phase14.py::test_mock_and_android_support_application_capabilities_apple_does_not` (constructs each provider directly, asserts capability-set membership, no network I/O) — **written, not yet run** | PENDING |
| **App deployment cannot bypass the existing command security pipeline** | `ApplicationService.deploy_to_device`/`remove_from_device` call the **unmodified** `CommandService.create_command` — same tenant-scoped device lookup, same capability gate, same idempotency-key mechanism, same `COMMAND_DISPATCH` job every admin-issued command already goes through. No parallel dispatch path. | Code review (`app/applications/service.py` imports and calls `CommandService` only; no direct provider or job-queue access) | PASS |
| **A deployment attempt cannot be silently double-dispatched** | Each attempt's `idempotency_key` is `f"app_deploy:{application_id}:{device_id}:{attempt}"` / `f"app_remove:...:{attempt}"`, using `CommandService`'s existing Phase 6 idempotency-key mechanism; `deploy_to_device` itself is a no-op once the deployment row is PENDING/INSTALLING/INSTALLED, so a repeated `POST /applications/{id}/deploy` call cannot trigger a second in-flight command for the same (device, application) pair. | `tests/test_applications_phase14.py::test_deploy_application_dispatches_install_command_via_mock_provider` (asserts a re-deploy call returns the same deployment id/attempt count) — **written, not yet run** | PENDING |
| **A required-app deployment is not retried unboundedly on failure** | `evaluate_required_applications` only auto-triggers a deployment when none exists yet (`deployment is None`) — a `FAILED` deployment is never automatically re-attempted by the compliance path; a further attempt requires an explicit admin `POST /applications/{id}/deploy`. | Code review (`app/applications/service.py::evaluate_required_applications`) — no dedicated test drives the FAILED-then-no-auto-retry path this phase | PASS (reasoning only) |
| **Cross-organization application/assignment/deployment access is impossible** | `ApplicationRepository`/`ApplicationAssignmentRepository`/`ApplicationDeploymentRepository` — every method takes `organization_id` and filters on it, no bare `get_by_id`; a foreign-org application 404s identically to a nonexistent one. | `tests/test_applications_phase14.py::test_org_b_cannot_see_or_deploy_org_a_application` — **written, not yet run** | PENDING |
| **Deploy/create/assign actions are gated by a permission distinct from read** | New `application.deploy` permission, confirmed absent from `HELP_DESK` by direct inspection of `app/rbac/permissions.py` (HELP_DESK has `application.read` only); `application.create`/`application.assign` also absent from HELP_DESK. | `tests/test_applications_phase14.py::test_help_desk_cannot_create_or_deploy_applications` — **written, not yet run** | PENDING |
| **A catalog entry's platform identifier cannot be mismatched or omitted** | `CreateApplicationRequest`'s `model_validator` rejects (422) any ANDROID app missing `package_name` or carrying `bundle_identifier`, and vice versa for IOS/IPADOS — enforced again by a DB `CHECK` constraint as defense in depth. | `tests/test_applications_phase14.py::test_create_application_rejects_mismatched_identifier` — **written, not yet run** | PENDING |
| **Duplicate catalog entries for the same platform+identifier are rejected** | `ApplicationRepository.exists_by_identifier` checked before insert (409 `APPLICATION_ALREADY_EXISTS`), backed by two DB unique constraints (`(org, platform, package_name)`, `(org, platform, bundle_identifier)`). | `tests/test_applications_phase14.py::test_duplicate_identifier_rejected` — **written, not yet run** | PENDING |
| **An application cannot be deleted while still assigned, silently orphaning assignments** | `delete_application` checks `count_for_application(...) > 0` and rejects with 409 `APPLICATION_HAS_ASSIGNMENTS` first — mirrors `PolicyService.delete_policy`'s identical precedent. | `tests/test_applications_phase14.py::test_assign_unassign_and_delete_blocked_while_assigned` — **written, not yet run** | PENDING |
| **A device's application inventory never fabricates an "installed" claim** | `GET /devices/{id}/applications` only ever reports a deployment status when a real `ApplicationDeployment` row (itself only ever created alongside a real `DeviceCommand`) exists; an assigned-but-never-deployed app is shown with no status, never a guessed "not installed"/"installed." | Code review (`app/applications/service.py::get_device_applications`, `app/applications/schemas.py::DeviceApplicationView.deployment` is optional) | PASS |
| **A deployment's status can never regress from a stale/replayed provider signal** | `reconcile_device_deployments` only ever reads the deployment's own `last_command_id`'s current `DeviceCommandStatus` — there is no second, independent status-update path (no provider webhook writes to `ApplicationDeployment` directly) that could race or replay against it. | Code review (`app/applications/service.py::reconcile_device_deployments` is the only writer of `ApplicationDeployment.status` besides creation) | PASS |
| **Bulk deploy cannot silently affect an unbounded number of devices in one request** | `_resolve_bulk_targets` checks `count_devices_for_group`/`count_for_organization` against `MAX_BULK_DEPLOY_TARGETS = 500` and rejects (400 `BULK_DEPLOY_TOO_LARGE`) before resolving/dispatching anything. | Code review (`app/applications/service.py::_resolve_bulk_targets`) — no dedicated test drives the >500-device path this phase (would require seeding 501 devices) | PASS (reasoning only) |
| **The deploy request body cannot specify zero or multiple conflicting targets** | `DeployApplicationRequest`'s `model_validator` requires exactly one of `device_id`/`device_ids`/`device_group_id`/`organization_wide` (422 otherwise). | `tests/test_applications_phase14.py::test_deploy_request_requires_exactly_one_target` — **written, not yet run** | PENDING |
| **Audit logging is bounded, not a per-reconciliation flood** | Deployment/removal *progress* (the reconciliation step, which runs on every compliance evaluation) is never audited — only the initial trigger (`APPLICATION_DEPLOYMENT_TRIGGERED`/`APPLICATION_REMOVAL_TRIGGERED`) and catalog/assignment CRUD are, matching Phase 13's own "bounded, not per-evaluation" precedent. The underlying `DeviceCommand` already emits its own audit trail for every state transition. | Code review (`app/applications/service.py`, `app/audit/models.py`) | PASS |

## 4. Discovered & Resolved Issues (during this phase's implementation)

1. **Considered and rejected**: extending `PolicyViolation`/
   `RemediationPolicyConfig` (Phase 13) to cover required-app failures
   directly. `PolicyViolation.policy_id` is a NOT NULL FK — application
   assignment is independent of policy assignment, so a required-app
   failure has no `Policy` row to attach one to without changing a prior
   phase's migration/schema contract, which the brief separately forbids.
   Resolved by reusing `DeviceCompliance`/`ComplianceResult` (the actual
   overall-compliance-status engine) directly instead — see
   `docs/architecture/phase-14.md` §6 for the full writeup. Caught during
   design, before writing the compliance-integration code, the same way
   Phase 13 caught its own "policy conflicts already solved" finding before
   building an unneeded resolver.
2. **`install_application`/`remove_application`'s Phase 6 placeholder
   signature** (`*args: Any, **kwargs: Any -> Any`) had to change to the
   same `(request: ProviderCommandRequest) -> ProviderCommandResult` shape
   every other provider method uses, across all three concrete providers
   (Mock/Android/Apple) plus the abstract base — a real signature change to
   dormant Phase 6 scaffolding, not new interface surface. Reviewed for
   whether this could break anything already calling the old signature:
   nothing did (`NotImplementedError` in every concrete provider, never
   invoked).
3. **Android app deployment's actual mechanism** (policy-level
   `applications` array patch, not a per-device push command) was
   confirmed against the same research-based, never-live-verified
   documentation standard every other real Android/Apple provider method
   in this codebase already carries, before writing the client methods —
   avoiding the mistake of assuming a per-device install command exists
   when Google's own documented API has no such thing.
4. **`CommandService.create_command`'s existing `actor_user_id: uuid.UUID
   | None` widening (Phase 13, for compliance remediation) is reused
   as-is** for application auto-deployment (`evaluate_required_applications`
   passes through the compliance evaluation's own `actor_user_id`, which is
   `None` for system-triggered evaluations) — no further widening needed,
   confirming Phase 13's own choice to make that parameter optional was the
   right generalization rather than a Phase-13-specific one-off.

## 5. Known Gaps / Deferred

- **No pytest run has happened for the new Phase 14 test file** — every
  PENDING row above reflects implemented-and-reviewed behavior, not
  confirmed behavior.
- **No test seeds 501+ devices to exercise the `BULK_DEPLOY_TOO_LARGE`
  cap** or the FAILED-then-no-auto-retry required-app path — both are
  code-review-only (PASS, reasoning only) this phase.
- **No real Apple app deployment** — documented capability gap (missing
  VPP-token integration), not a security hole: the capability gate rejects
  it before any provider call, exactly as it does for Apple `REBOOT`/
  `POLICY_SYNC`/`UNENROLL` already.
- **Android app deployment is policy-wide, not device-scoped** — an
  inherent property of the real Android Management API's declarative
  app-policy model (see `docs/architecture/phase-14.md` §5/§13), not a
  Phase 14-introduced weakness; identical in kind to `POLICY_SYNC`'s
  existing caveat since Phase 6.
- **No async fanout for bulk deploy** — synchronous, capped at 500
  devices; see `docs/architecture/phase-14.md` §4/§13.
- Every other gap already documented in `docs/security/phase-6-security-review.md`
  §4, `docs/security/phase-12-security-review.md` §5, and
  `docs/security/phase-13-security-review.md` §5 (no live Postgres, no
  real provider credentials, no Docker, no real ADE/VPP) is unchanged and
  still applies — application deployment flows through the exact same
  Mock/Android/Apple provider paths as every admin-issued command, never a
  new dispatch mechanism.
