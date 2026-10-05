# Phase 6 — Remote Device Command & Management Security Review

## 1. Scope

Remote device commands (lock/reboot/wipe/refresh-info/policy-sync), the
platform provider abstraction, and the Mock/Android/Apple providers
introduced in Phase 6. Builds on
`docs/security/multi-tenancy-security-review.md` (tenant isolation),
`docs/security/rbac.md` (permission model), `docs/security/device-security.md`
(Phase 4 device inventory security), and `docs/security/phase-5-security-review.md`
(enrollment/credential security) — this review focuses on what's new. Full
design rationale in `docs/architecture/device-commands.md`.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Command RBAC** | `device.lock`/`device.wipe` (pre-existing, reused) plus new `device.command.{reboot,refresh,policy_sync,cancel}`, integrated into the existing `app/rbac/permissions.py` system, additive-only role grants. HELP_DESK gets lock+refresh only (matches its documented "can lock, cannot wipe" philosophy). | `tests/test_command_rbac.py` (HELP_DESK, SECURITY_ADMIN, AUDITOR matrices) | PASS |
| **Tenant isolation — command creation** | `CommandService.create_command` resolves the device via the same org-scoped `DeviceRepository.get_for_organization` as every other device operation; a cross-tenant device_id is `404 DEVICE_NOT_FOUND`. | `tests/test_command_tenant_isolation.py::test_org_a_cannot_issue_command_to_org_b_device` | PASS |
| **Tenant isolation — command read/cancel** | `DeviceCommandRepository` requires `organization_id` on every method — no bare `get_by_id`. A command in a different org is `404`, never a `403` that would confirm existence. | `tests/test_command_tenant_isolation.py::test_org_a_cannot_read_org_b_command`, `test_org_a_cannot_cancel_org_b_command` | PASS |
| **Forged `X-Organization-ID`** | The commands router uses the same `require_device_permission` (`require_current_organization_permission`, header-based, re-validates real membership every request) as every other Phase 4/5 module — not a parallel, possibly-weaker check. | `tests/test_command_tenant_isolation.py::test_forged_organization_header_rejected_for_commands` | PASS |
| **Device state validation** | A command can only be issued to a device with `enrollment_status == ENROLLED` — `409 DEVICE_NOT_ENROLLED` otherwise, before any provider is even resolved. | `tests/test_commands.py::test_command_for_unenrolled_device_is_409` | PASS |
| **Wipe — explicit confirmation** | `IssueWipeCommandRequest.confirm: Literal[True]` is a required field — an empty/minimal request body cannot trigger a wipe (`422` on omission). | `tests/test_command_wipe_safeguards.py::test_wipe_missing_confirm_rejected` | PASS |
| **Wipe — mandatory idempotency** | `idempotency_key` is required specifically on the wipe request (optional elsewhere) — a client retry can never double-dispatch a destructive command. | `tests/test_command_wipe_safeguards.py::test_wipe_missing_idempotency_key_rejected`, `test_wipe_retry_with_same_key_returns_same_command_not_a_duplicate` | PASS |
| **Wipe — immediate credential revocation** | A wipe reaching `SENT` clears `Device.device_secret_hash` immediately, not deferred to a completion signal this codebase may never receive from a real provider. | `tests/test_command_wipe_safeguards.py::test_wipe_clears_device_credential_immediately` | PASS |
| **Idempotency — general** | `UniqueConstraint(organization_id, device_id, idempotency_key)`, NULLs distinct; SELECT-before-INSERT plus `IntegrityError` fallback for the concurrent-request race (documented, accepted window — see `docs/architecture/device-commands.md` §5). | `tests/test_commands.py::test_repeated_idempotency_key_returns_same_command` | PASS |
| **Command expiration** | `expires_at` set at creation (`device_command_expire_hours`, default 24); evaluated lazily at read time (`_effective_command_status`), same tzinfo-safe pattern as Phase 5's `_effective_token_status`. The backend, not the frontend, is authoritative — an expired command cannot be cancelled or re-read as `SENT`. | `tests/test_command_state_machine.py::test_sent_command_expires_lazily_on_read` | PASS |
| **State transition validation** | `cancel_command` legal only from `SENT` — `409 INVALID_COMMAND_TRANSITION` from any terminal state. No endpoint allows arbitrary status mutation; every transition goes through `CommandService`, never a raw PATCH. | `tests/test_command_state_machine.py::test_cannot_cancel_a_completed_command` | PASS |
| **No silent mock fallback** | A real provider (Android/Apple) that lacks required credentials raises `PlatformProviderUnavailableError` at construction — the registry never silently substitutes `MockManagementProvider`. The resulting command is `FAILED` with a clear `error_code`, `provider_name` reflecting the *attempted* real provider (never `"mock"`), never a silent success. | `tests/test_command_platform_provider_unavailable.py::test_real_provider_mode_without_credentials_fails_loudly` | PASS |
| **Mock provider labeling** | Every `MockManagementProvider` response string is prefixed "Simulated:" — never phrased as a real action ("Simulated: device lock command accepted" not "Device locked"), per the pre-existing mock-adapter policy in `docs/architecture/android-enterprise.md` §5. | Code review (`app/platform/mock/provider.py`) | PASS |
| **Capability mismatch rejected safely** | `CommandService` checks `PlatformCapability` membership before any dispatch attempt — an unsupported command for a platform (e.g. `REBOOT` against `AppleManagementProvider`, which genuinely lacks it) fails as `COMMAND_TYPE_NOT_SUPPORTED`, never silently ignored or misrouted. | `tests/test_apple_provider.py::test_reboot_is_rejected_as_unsupported` | PASS |
| **No secrets logged or persisted in command records** | `DeviceCommand.result` stores only sanitized provider responses (operation names, command UUIDs) — never a service-account private key, an APNs signing key, or a device's own `device_secret_hash`. Android/Apple provider credentials are read from `Settings` (env-sourced) and never written to `payload`/`result`/`error_message` fields, nor to structured logs. | Code review (`app/platform/android/provider.py`, `app/platform/apple/provider.py`, `app/commands/service.py`) | PASS |
| **Audit trail completeness** | Every command creation, dispatch outcome (dispatched/completed/failed), cancellation, and lazy expiration logs an `AuditLog` entry via the existing `AuditRepository.log_event` — never constructed ad hoc. Metadata carries `command_id`/`command_type`/`status`, never a secret. | Code review (`app/commands/service.py`) + audit event types in `app/audit/models.py` | PASS |
| **CSRF** | Command endpoints are cookie+CSRF admin-authenticated like every other Phase 4/5 mutating endpoint — no device-credential channel exists here (commands are issued *by* an admin, never presented *by* a device), so no new `EXEMPT_PATH_PATTERNS` entry was needed, unlike Phase 5's heartbeat fix. | Code review (`app/auth/middleware.py` unchanged; `app/commands/router.py` module docstring) | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **Provider-resolution failure would have bypassed the audit trail
   entirely.** The initial design resolved the platform provider *before*
   creating the `DeviceCommand` row, meaning a misconfigured real provider
   would raise an unhandled `503` with no persisted record an attempt was
   ever made. Caught during implementation, before any test was written
   against the flawed shape — fixed by moving provider resolution and
   capability checking inside `CommandService._dispatch`, wrapped in the
   same try/except as the dispatch call itself, so every command attempt
   past basic device/enrollment validation always produces an audited row
   (`PENDING` → `FAILED`), matching the "every command must produce an
   audit trail" requirement literally rather than only for the
   already-successful path.
2. **Mock provider's default synchronous-COMPLETED behavior made the
   `SENT` state functionally unreachable in tests**, which would have made
   `cancel_command`'s only legal source state (`SENT`) untestable via the
   default test provider. Resolved by adding a test-only `simulate_sent`
   payload key to `MockManagementProvider` — deliberately **not** exposed
   through any public request schema, so an admin can never instruct a
   real provider to "simulate" anything; state-machine tests reach it by
   constructing `CommandService` directly against the same test database
   session, not through the HTTP API.

## 4. Known Gaps / Deferred to Phase 7+

- **No completion-callback mechanism** — see
  `docs/architecture/device-commands.md` §7. A real-provider command can
  remain `SENT` indefinitely; there is no Android Pub/Sub subscriber and
  no Apple MDM check-in HTTPS endpoint. This is the largest deliberate gap
  in this phase.
- **Android and Apple providers were never live-verified.** No Docker, no
  real Google Cloud service account, no real APNs certificate exists in
  this development environment (confirmed: `docker --version` fails).
  Both providers are unit-tested only against a mocked HTTP transport
  (`tests/test_android_provider.py`, `tests/test_apple_provider.py`) —
  shape-correctness, not live-integration proof. Neither provider is ever
  constructed by default (`platform_provider_mode` defaults to `"mock"`).
- **No device has a real platform-level identifier.** `ProviderDeviceRef.provider_device_id`
  (the Android Management API device resource name, repurposed as the
  Apple APNs push token) is never populated anywhere in this codebase —
  Phase 5's enrollment is a synthetic backend-only identity, not genuine
  Android Enterprise or Apple MDM enrollment. Every real-provider dispatch
  therefore fails for a second, independent reason beyond missing
  credentials: no device-level platform identity exists to address.
- **`POLICY_SYNC` is unverified for Apple** and left out of
  `AppleManagementProvider.supported_capabilities` rather than guessed —
  see `docs/apple/mdm-capabilities.md`.
- **Docker/Postgres was not available in this environment** and was not
  verified for this phase either — migration `0006_phase6_device_commands.py`
  follows the same pattern as 0004/0005 (neither ever run against real
  Postgres) but has not executed against a live database.
- The pre-existing absence of any `session.commit()` call anywhere in
  `app/` (repositories only `flush()`) was not introduced by, or fixed
  in, Phase 6 — unchanged from every prior phase.
