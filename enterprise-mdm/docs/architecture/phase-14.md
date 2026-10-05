# Phase 14 — Enterprise Application Management

Builds on completed Phases 1-13 (auth/RBAC/multi-tenancy, devices,
enrollment, device groups, policies, compliance, remote commands,
background jobs, real Android/Apple provider integration, provider-device
links, production device management, advanced policy enforcement +
automated remediation — see `docs/architecture/phase-13.md` for the most
recent). Repo root: `enterprise-mdm/`. Brief: a 30-section prompt, same
"no re-testing 1-13, no planning doc, go directly to development" execution
rules as Phases 7-13.

## 1. What already existed vs. what Phase 14 built

`app/applications/__init__.py` and the frontend's `app/applications/` and
`features/applications/` directories were empty placeholders — Phase 1's
module scaffolding reserved the name but nothing was implemented. There
was no application/catalog/software schema, model, or migration anywhere
in the codebase. Phase 14 is a genuinely new module, not an extension of
prior work — but it is built almost entirely out of *existing*
infrastructure (jobs, commands, RBAC, audit, tenant-scoping patterns), per
the brief's explicit "reuse jobs/commands/devices/compliance/audit
logging/RBAC — do NOT create parallel systems" instruction.

Two permission constants (`APP_READ`/`APP_CREATE`/`APP_UPDATE`/
`APP_DELETE`/`APP_ASSIGN`) and two provider methods
(`install_application`/`remove_application`, `*args/**kwargs` stubs raising
`NotImplementedError` in every concrete provider) already existed in
`app/rbac/permissions.py` and `app/platform/base/provider.py` since Phase 6
— reserved-but-unimplemented scaffolding, exactly like the empty
`app/applications` package. This phase is the first to give them real
bodies.

## 2. Application catalog

`Application` (`app/applications/models.py`) — one row per catalog entry.
Platform-specific identifier fields are kept isolated, not collapsed into
one generic `identifier` string (brief §2's explicit instruction):
`package_name` for `platform=ANDROID`, `bundle_identifier` for
`platform in (IOS, IPADOS)`. Enforced twice — a `CHECK` constraint at the
schema level (defense in depth) and a `model_validator` on
`CreateApplicationRequest` (the actual 422-producing check a caller sees).
`platform` reuses the existing `device_platform` Postgres enum (Phase 4)
rather than a new one.

`managed: bool` marks whether this codebase's own
deploy/remove commands apply to a catalog entry — a `managed=False` row is
reference/inventory metadata only, matching brief §2's "keep track of
managed vs. unmanaged" spirit without inventing a status this phase can't
back with a real signal (see §9 below).

## 3. Assignment — reused, not duplicated

`ApplicationAssignment` (same file) is deliberately shaped exactly like
`PolicyAssignment` (Phase 5): `organization_id`, `application_id`,
`scope_type`, `scope_id`, unique on `(application_id, scope_type,
scope_id)`. `scope_type` is **the same `PolicyScopeType` enum**
(`ORGANIZATION`/`USER`/`DEVICE_GROUP`/`DEVICE`), imported directly rather
than redefined — brief §3 says "reuse existing scope patterns," and a
second near-identical four-value enum would only be a maintenance trap (a
new fifth scope type added to one and not the other). `ApplicationService.
_validate_scope` is a line-for-line copy of `PolicyService._validate_scope`'s
logic (same four branches, same repositories).

The one place application assignment genuinely differs from policy
assignment: **resolution is a union, not a single-winner precedence
chain**. `PolicyResolutionService.resolve_for_device` picks the *one*
highest-precedence ACTIVE policy, because a device can only sensibly be
governed by one policy at a time. Multiple required (or optional)
applications from different scopes are not mutually exclusive the same
way — a device can legitimately have a REQUIRED app from an organization-
wide assignment AND another REQUIRED app from its device group AND a third
from a device-specific assignment, all simultaneously in effect.
`ApplicationService.resolve_assignments_for_device` therefore unions every
assignment across all four scope levels, with one tie-break: if the same
application is assigned at two scopes with different requirements, REQUIRED
always wins (an org-wide OPTIONAL assignment can never downgrade a more
specific REQUIRED one).

## 4. Deployment — literally "assignment -> command -> provider -> result"

Per brief §5/§6, deployment is dispatched through the **exact same**
`DeviceCommand`/`CommandService` machinery every other remote device action
(lock/wipe/reboot/policy-sync/unenroll) already uses:

- Two new `DeviceCommandType` values: `INSTALL_APPLICATION`,
  `UNINSTALL_APPLICATION` (`app/commands/models.py`).
- Two new `PlatformCapability` values: `APPLICATION_INSTALL`,
  `APPLICATION_REMOVE` (`app/platform/base/capabilities.py`), wired into
  `CommandService`'s existing `_CAPABILITY_BY_TYPE` gate — an
  unsupported-for-this-provider deploy is rejected with the same
  `COMMAND_TYPE_NOT_SUPPORTED` error every other command type already
  produces, before a command row even exists.
- `install_application`/`remove_application` on
  `PlatformManagementProvider` had their Phase 6 placeholder signature
  (`*args, **kwargs -> Any`) changed to the same
  `(request: ProviderCommandRequest) -> ProviderCommandResult` shape every
  other real command method uses — the minimum change needed to actually
  wire them in, not a redesign of the interface.
- No new `JobType`, no new worker code path: `CommandService.create_command`
  already enqueues a `COMMAND_DISPATCH` job for every command it creates
  (Phase 8) — an app-install/uninstall command rides that unchanged. "The
  deployment job" the brief asks for (§5, §12) **is** `COMMAND_DISPATCH`;
  building a second job type for the same purpose would be exactly the
  "parallel system" the brief says not to create.

`ApplicationDeployment` (one row per `(organization, device, application)`,
`app/applications/models.py`) is the queryable "current state of this
(device, app) pair" anchor — modeled on `PolicyViolation`'s own shape
(Phase 13): re-opened on a fresh deploy attempt after FAILED/REMOVED,
never duplicated. The **history** of every attempt lives in the
`DeviceCommand` rows themselves (one new row per attempt, each with its own
idempotency key `f"app_deploy:{application_id}:{device_id}:{attempt}"`,
DeviceCommand's own append-only, never-mutated-backward convention) — full
traceability (brief §15, Application -> Assignment -> Deployment ->
Command -> Provider result) is: `ApplicationDeployment.assignment_id` ->
`ApplicationAssignment`; `ApplicationDeployment.last_command_id` ->
`DeviceCommand`; `GET /devices/{id}/commands` for that device's full
command history including every past install/uninstall attempt.

Deployment status (`PENDING`/`INSTALLING`/`INSTALLED`/`FAILED`/`REMOVED`,
brief §8) is advanced by `ApplicationService.reconcile_device_deployments`,
called from the exact same per-evaluation choke point Phase 13's own
violation reconciliation already runs from (`ComplianceService.
evaluate_device`, itself triggered by every heartbeat/manual-evaluate/
policy-reevaluation-fanout) — no new trigger mechanism, no timer, no
polling loop. It checks whether each in-flight deployment's
`last_command_id` has reached a terminal `DeviceCommandStatus` and maps
`COMPLETED` -> `INSTALLED` (or `REMOVED` for an uninstall command) and
every other terminal status -> `FAILED`.

**Idempotency**: `deploy_to_device` is a no-op (returns the existing row
unchanged) for a deployment already `PENDING`/`INSTALLING`/`INSTALLED` —
only `FAILED`/`REMOVED`/brand-new actually dispatch a fresh command. This
mirrors `CommandService.create_command`'s own idempotency-key precedent at
the deployment layer, one level up.

### Bulk deployment (brief §13)

`POST /applications/{id}/deploy` accepts exactly one of `device_id`,
`device_ids` (explicit list), `device_group_id`, or `organization_wide`
(enforced by a Pydantic validator, 422 otherwise). Every target device
still gets its own `ApplicationDeployment` row, its own `DeviceCommand`,
and its own independent status — never one shared dispatch. Unlike Phase
5's policy-assignment fanout (`POLICY_REEVALUATION_FANOUT`, an async,
paginated background job specifically because a policy-scope change can
cover an unbounded number of devices), a device-group/organization-wide
deploy is resolved and dispatched **synchronously within the issuing
request**, capped at `MAX_BULK_DEPLOY_TARGETS = 500` devices — the caller
gets back the full per-device deployment-result list immediately, which is
a more useful admin response for "did my deploy actually work" than a job
id to poll. A target larger than the cap is rejected with
`BULK_DEPLOY_TOO_LARGE`, asking the caller to split it into explicit
`device_ids` batches. See §12 (Known limitations) for why no async fanout
was built for this instead.

## 5. Provider capabilities — honest, not fabricated (brief §7/§23)

- **Mock** (`app/platform/mock/provider.py`): supports both new
  capabilities, resolves deterministically to `COMPLETED` like every other
  mock command — this is what every test in this phase (and every prior
  phase's default test configuration) actually exercises.
- **Android** (`app/platform/android/provider.py`): supports both, backed
  by a genuine (never live-verified — see the module's own long-standing
  caveat) mapping onto the real Android Management API. Critically, the
  Android Management API has **no per-device "install this app now" push
  command** the way `issueCommand(type=LOCK)` exists for lock/reboot/wipe —
  app deployment is declarative via the enterprise policy resource's own
  `applications` array; a device picks the change up on its next policy
  sync. `install_application`/`remove_application` therefore read-modify-
  write the same `enterprises/{id}/policies/default` resource
  `apply_policy` already assumes every device in this codebase is on (two
  new `AndroidApiClient` methods, `get_policy`/`patch_policy`), and report
  `SENT` (not `COMPLETED`) — the same "best-effort, not guaranteed
  immediate" honesty `POLICY_SYNC` already carries for the identical
  reason. This is a real, researched mapping onto Google's documented
  Policy.applications field, not a guess — but it does mean, same as
  `POLICY_SYNC`, that the effect lands on every device sharing that policy,
  not narrowly on the one device the command was issued against. Still
  gated behind `_require_provider_device_id` — an unlinked device (every
  device in this codebase today, since no real Android enrollment flow
  produces a provider-device link — see Phase 9/11) fails fast with a
  clear, distinct error, exactly like every other real Android dispatch
  method.
- **Apple** (`app/platform/apple/provider.py`): does **not** support either
  capability. Apple's real mechanism (the `InstallApplication`/
  `RemoveApplication` MDM commands) requires an app manifest/IPA URL and,
  for App Store apps, a VPP (Volume Purchase Program) content token —
  neither of which this codebase has any integration for, and whose
  payload schema was not part of this phase's research pass. Per the
  brief's explicit "do not fabricate unsupported operations" instruction,
  `APPLICATION_INSTALL`/`APPLICATION_REMOVE` are simply absent from
  `AppleManagementProvider._SUPPORTED_CAPABILITIES` — `CommandService`'s
  existing capability gate rejects an attempted Apple app deployment with
  `COMMAND_TYPE_NOT_SUPPORTED` before any provider method is ever called,
  the same mechanism that already rejects Apple `REBOOT`/`POLICY_SYNC`/
  `UNENROLL` today.

## 6. Compliance + remediation integration — reused at the right layer

Brief §10/§11 ask for "required app missing -> policy violation -> reuse
Phase 13 remediation." A literal reuse of `PolicyViolation`/
`RemediationPolicyConfig` was evaluated and rejected — worth documenting
why, the same way Phase 13 itself documented "policy conflicts were already
solved" instead of building an unneeded conflict resolver:

- `PolicyViolation.policy_id` is a **NOT NULL** FK — every violation is
  structurally tied to one resolved `Policy`. Application assignment is
  independent of policy assignment (a device can have required apps with
  no policy assigned at all, or vice versa) — a required-app failure has
  no `Policy` row to attach a `PolicyViolation` to without changing that
  table's Phase 13 contract, which the brief separately forbids ("do not
  modify prior migrations").
- `RemediationPolicyConfig.command_type` is a single, no-payload command
  per *policy* — it has no field for "which package/bundle identifier,"
  because that's inherently per-*application* information, not
  per-*policy* information. Extending its schema to carry an application
  reference would be modifying Phase 13's schema for a concept it was
  never designed to express, for the sake of forcing a fit.

So Phase 14 reuses the two things that actually generalize, each at its
own correct layer:

- **Compliance status** — the brief's real ask ("this device is
  non-compliant because a required app is missing") is reused *directly*:
  `ComplianceService.evaluate_device` (Phase 5) calls
  `ApplicationService.evaluate_required_applications`, whose returned
  `RuleResult` (or `None`, if the device has no REQUIRED application
  assignments at all — the same "rule doesn't apply" convention every
  function in `app/compliance/rules.py` already follows) is appended to
  the *exact same* `results` list every existing rule feeds into, before
  `_overall_status`/`DeviceCompliance`/`ComplianceResult` are computed.
  `rules.py` itself is untouched — its functions are pure `(device,
  config) -> RuleResult | None`, with no repository access, and adding an
  application-assignment-aware check there would have broken that
  invariant for no reason; the extra `RuleResult` is appended from
  `evaluate_device` instead, which already has the repository access it
  needs.
- **Remediation** — "missing required app -> install command" is the
  deployment workflow itself, triggered directly from
  `evaluate_required_applications` the first time a required app is found
  missing for a device. This needs no `RemediationPolicyConfig` opt-in:
  installing an app an admin explicitly assigned as REQUIRED is the direct,
  intended effect of that assignment, not a discretionary automated action
  that needs the same "must be an explicitly safe, non-destructive command
  the admin opted into" safeguard Phase 13 built for arbitrary policy-wide
  remediation. `deploy_to_device`'s own idempotency (no-op once
  PENDING/INSTALLING/INSTALLED) makes it safe to call on every evaluation
  without risking a redundant dispatch; a deployment that reaches `FAILED`
  is **not** automatically retried by this path — a second attempt
  requires an explicit admin action (`POST /applications/{id}/deploy`
  again), mirroring Phase 13's own "`REMEDIATION_FAILED` needs a human"
  line, just without a numeric attempt counter to enforce it, since there
  is only ever one automatic attempt to begin with.

`reconcile_device_deployments` and `evaluate_required_applications` are
both called from `evaluate_device` in that order (reconcile first, so a
just-completed install is reflected before the requirement check runs in
the same evaluation cycle) — one new call site in an already-existing
method, not a second evaluation path.

## 7. API

```
GET    /applications                                [org] application.read
POST   /applications                                [org] application.create
GET    /applications/{id}                           [org] application.read
PATCH  /applications/{id}                           [org] application.update
DELETE /applications/{id}                           [org] application.delete
POST   /applications/{id}/assign                    [org] application.assign
POST   /applications/{id}/unassign                  [org] application.assign
GET    /applications/{id}/assignments               [org] application.read
POST   /applications/{id}/deploy                    [org] application.deploy
GET    /applications/{id}/deployments                [org] application.read
POST   /applications/{id}/devices/{device_id}/remove [org] application.deploy
GET    /devices/{id}/applications                    [org] device.read
```

`PATCH`, not `PUT` (brief §16 lists `PUT`) — kept consistent with this
codebase's unbroken existing convention (every other "update" route,
`app/policies/router.py` included, is `PATCH`); a full-replace `PUT` was
never adopted anywhere else in this API, and introducing one only for this
resource would be the inconsistency, not the fix.

The device-application-inventory route lives on a second router sharing
the `/devices` prefix (`app/applications/router.py::
device_applications_router`), exactly like `app/commands/router.py`
already shares that prefix with `app/devices/router.py` — not nested under
`/applications`, since a device's own inventory is naturally read from the
device's own page.

## 8. Device application inventory (brief §9) — never fabricated

`GET /devices/{id}/applications` returns every application **resolved via
assignment** (`resolve_assignments_for_device`) for that device, joined
with its `ApplicationDeployment` row if one exists. This is deliberately
NOT a device-reported "list of installed apps" — no signal exists anywhere
in this codebase (no REFRESH_INFO field, no check-in payload, no provider
event) that reports a device's actual installed-application list, and
inventing one would violate the brief's own explicit "do not invent
installed applications" instruction. What this codebase *can* honestly
report is "which apps are assigned to this device, and what deployment
outcome did our own attempt to install them observe" — which is exactly
what `ApplicationDeployment.status` is. An app with no deployment row at
all (never assigned, or assigned but never deployed) is shown with no
status, not a fabricated "not installed."

## 9. RBAC

One new permission, `application.deploy` — deliberately separate from the
pre-existing `application.assign` (added Phase 6, unused until now): a
user who can assign an application to a scope and a user who can actually
dispatch a real command to real devices are different-weight actions, the
same split `POLICY_ASSIGN` vs. `COMPLIANCE_EVALUATE` already draws between
"configure" and "act now." Granted to `ORGANIZATION_ADMIN` and
`SECURITY_ADMIN` (compliance/remediation is squarely `SECURITY_ADMIN`'s
domain per its own role description); `HELP_DESK` and `AUDITOR` get
`application.read` only (`HELP_DESK` didn't have it before this phase —
added since a help-desk operator viewing a device's required-app status is
exactly the kind of read this role already gets for policies/compliance).

## 10. Tenant isolation

Every repository method added in this phase (`ApplicationRepository`,
`ApplicationAssignmentRepository`, `ApplicationDeploymentRepository`)
takes `organization_id` and filters on it — no bare `get_by_id`, matching
every prior phase's rule. A cross-organization application/assignment/
deployment lookup 404s identically to a nonexistent one.

## 11. Audit logging

`APPLICATION_CREATED`/`UPDATED`/`DELETED`/`ASSIGNED`/`UNASSIGNED`/
`DEPLOYMENT_TRIGGERED`/`REMOVAL_TRIGGERED` — seven new `AuditEventType`
values. Deployment/removal *progress* (the reconciliation step) is
deliberately **not** separately audited: the underlying `DeviceCommand`
already emits `DEVICE_COMMAND_CREATED`/`DISPATCHED`/`COMPLETED`/`FAILED`
for every state change that command goes through, and duplicating that
here would just double-log the same transition under a second name — the
identical "bounded, not per-evaluation" principle Phase 13 established for
its own audit events.

## 12. Frontend

`frontend/types/application.ts` + `frontend/lib/applications.ts` (new) —
catalog/assignment/deployment/device-inventory hooks, same
useQuery/useMutation + query-key-invalidation shape as
`frontend/lib/policies.ts`/`violations.ts`.
`frontend/app/(app)/applications/page.tsx` (new) — catalog list + inline
create form (name/platform/identifier). `frontend/app/(app)/applications/
[id]/page.tsx` (new) — details, assignment management, single-device
deploy, and deployment history. `frontend/app/(app)/devices/[id]/page.tsx`
— added an "Applications" section (requirement + deployment status per
resolved app), reusing `GET /devices/{id}/applications`. The existing
device-detail "Compliance" section required **no changes at all** — it
already renders `compliance.details` generically by `code`/`outcome`/
`message`, so the new `REQUIRED_APPLICATIONS_INSTALLED` rule result shows
up there automatically (brief §22, "reuse Phase 13 compliance views," is
satisfied by not touching that section rather than by adding to it).
`frontend/app/(app)/layout.tsx` — added an "Applications" nav item between
Policies and Compliance.

Bulk deploy (device group / organization-wide) is API-only this phase —
the detail page's deploy form only exposes single-device deploy inline;
documented as a known limitation below rather than built out with a
device/group picker UI under this effort budget.

## 13. Known limitations (deliberate scope boundaries, not oversights)

- **No async fanout for bulk deploy** — capped at `MAX_BULK_DEPLOY_TARGETS
  = 500`, resolved synchronously within the request. See §4.
- **No real NOTIFY-equivalent for deployment failures** — a `FAILED`
  deployment is visible via the API/UI but nothing pages/emails anyone;
  consistent with Phase 13's own "no real NOTIFY delivery channel" gap.
- **No automatic retry of a `FAILED` required-app deployment** — by
  design (§6); requires an explicit admin re-deploy.
- **Android app deployment affects the shared `default` policy, not
  narrowly the target device** — an inherent property of the real Android
  Management API's declarative app-policy model, not a Phase 14 bug; the
  exact same caveat `apply_policy`/`POLICY_SYNC` has carried since Phase 6.
- **No real Apple app deployment** — documented capability gap, not
  fabricated (§5).
- **`ApplicationPolicyConfig` (`allowed_app_ids`/`denied_app_ids`/
  `block_unapproved_installs`, `app/policies/schemas.py`, pre-existing
  since Phase 5) is unrelated to this phase's catalog** and was not wired
  up — it has no installed-app inventory signal to check against (see §8),
  and building one was out of this phase's scope.
- **Bulk-deploy UI** (device group / organization-wide picker) not built
  this phase — API-only; see §12.
- Every gap already documented in prior phases' security reviews (no live
  Postgres, no real provider credentials, no Docker, no real ADE/VPP) is
  unchanged and still applies.

## 14. Migration

`0013_phase14_applications.py`: new `applications`,
`application_assignments`, `application_deployments` tables; new
`application_requirement`/`application_deployment_status` enums; additive
`ALTER TYPE` on `device_command_type` (`INSTALL_APPLICATION`/
`UNINSTALL_APPLICATION`) and `audit_event_type` (7 new values). References
the existing `device_platform` and `policy_scope_type` enums with
`create_type=False`, matching the `0005` migration's own
`device_platform_ref` precedent. No changes to `policies`,
`policy_versions`, `policy_assignments`, `policy_violations`,
`device_commands`, or `background_jobs` table schemas.
