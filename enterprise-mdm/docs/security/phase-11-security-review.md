# Phase 11 — Real Enrollment & Provider Activation Security Review

## 1. Scope

Real provider-side enrollment-token provisioning, automatic
`ProviderDeviceLink` creation (both device-supplied at registration and
provider-reported via Android webhook check-in), and enrollment-revocation
link cleanup, introduced in Phase 11. Builds on
`docs/security/phase-9-security-review.md` (Android webhook auth/
resolution) and `docs/security/phase-10-security-review.md`
(`ProviderDeviceLink` admin semantics) — this review focuses on what's
new. Full design rationale in `docs/architecture/phase-11.md`.

**This review is code-review-only, same caveat as Phase 10's.** Targeted
tests exist (`tests/test_enrollment_provider_linking.py`,
`tests/test_android_enrollment_provisioning.py`) but this document does
not assert they have been run — every row tied to one is marked PENDING
until confirmed by an actual pytest run.

## 2. Static verification actually performed

- `python -c "import app.main"` — succeeds.
- `python -m ruff check app tests` — **all checks passed** (run after
  fixing two line-length violations this phase introduced, in
  `app/enrollment/router.py` and the new test files).
- `python -m mypy app --strict` — **success, no issues found in 152
  source files** (up from Phase 10's 146 — this phase added
  `app/platform/base/provider_names.py` and two new test files aren't
  counted by an app-only mypy run).

## 3. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **A device cannot claim a provider identity another device in the same org already owns** | `register_device` checks `ProviderDeviceLinkRepository.exists_for_provider` and rejects (409 `DEVICE_ALREADY_ENROLLED`) **before** creating any Device row — no orphaned duplicate device is ever created for the rejected attempt. | `tests/test_enrollment_provider_linking.py::test_duplicate_provider_device_id_in_same_org_rejected` — **written, not yet run** | PENDING |
| **The same provider_device_id is legitimately reusable across different organizations** | Uniqueness is enforced by `uq_provider_device_link` scoped to `(organization_id, provider_name, provider_device_id)` — the existing Phase 8 constraint, unchanged. Two different organizations linking the same identifier is not an error. | `tests/test_enrollment_provider_linking.py::test_same_provider_device_id_allowed_across_different_organizations` — **written, not yet run** | PENDING |
| **A device cannot claim a provider identity for a platform that has no such provider** | `platform_to_provider_name(OTHER)` returns `None`; `register_device` rejects `provider_device_id` for that platform with 400 `PROVIDER_LINKING_NOT_SUPPORTED` rather than silently accepting an unresolvable link. | `tests/test_enrollment_provider_linking.py::test_provider_device_id_rejected_for_other_platform` — **written, not yet run** | PENDING |
| **apple_push_token cannot be attached to a non-Apple device** | Rejected 400 `APPLE_PUSH_TOKEN_NOT_APPLICABLE` whenever the derived provider isn't "apple" — prevents a nonsensical or spoofed cross-platform field combination from ever reaching the database. | `tests/test_enrollment_provider_linking.py::test_apple_push_token_rejected_for_android_platform` — **written, not yet run** | PENDING |
| **A revoked enrollment cannot leave a stale provider link a future device could collide with** | `revoke_enrollment` deletes any `ProviderDeviceLink` for the device (`get_any_link_for_device`) — the Device row and its full audit/command history are preserved; only the provider association is removed, matching Phase 10's manual-unlink semantics exactly. | `tests/test_enrollment_provider_linking.py::test_revoke_enrollment_deletes_provider_link_but_keeps_device` — **written, not yet run** | PENDING |
| **Android webhook auto-enrollment cannot be triggered by an inactive enrollment token** | `resolve_provider_checkin` applies the same `_effective_token_status` lazy-expiry/exhaustion check `register_device` already applies to every registration — an expired, revoked, or exhausted token yields no auto-enrollment, only a logged `DEVICE_ENROLLMENT_FAILED` security event. | `tests/test_android_enrollment_provisioning.py::test_webhook_auto_enroll_rejects_revoked_token` — **written, not yet run** | PENDING |
| **Auto-enrollment resolves to the correct organization, never a guess** | Resolution is keyed on `provider_enrollment_reference` — a provider-issued, globally unique resource name (Android's `enrollmentTokenName`) — via a single unscoped point lookup with no ambiguity case (unlike `get_by_provider_device_id_unscoped`, which does need one, since a provider's own credential resource name cannot collide the way an operator-supplied device identifier theoretically could). An unmatched reference yields no auto-enrollment, never a fallback guess. | `tests/test_android_enrollment_provisioning.py::test_webhook_auto_enrolls_previously_unseen_device` — **written, not yet run** | PENDING |
| **Auto-enrollment still honors admin-configured approval gating** | `resolve_provider_checkin` reads the resolved token's own `require_approval` and produces `PENDING`, not `ENROLLED`, exactly like `register_device` — an org that requires manual approval for self-registered devices gets the same guarantee for provider-auto-enrolled ones. | `tests/test_android_enrollment_provisioning.py::test_webhook_auto_enroll_respects_require_approval` — **written, not yet run** | PENDING |
| **The extra outbound Android API call only ever fires for a genuinely unknown device** | `_try_auto_enroll_android_device` is only reached in the webhook's existing `else` branch — i.e. only when `get_by_provider_device_id_unscoped` already found no link. Every subsequent check-in from an already-linked device resolves in the pre-existing Phase 9 branch and never triggers a `devices.get` call. | Code review (`app/platform/android/router.py`) | PASS |
| **A failed/unconfigured provider lookup during auto-enrollment never crashes the webhook** | `_try_auto_enroll_android_device` gates on `settings.android_service_account_json`/`android_enterprise_id` being set at all (mirroring `check_android_health`'s own gate) and catches `PlatformProviderUnavailableError`/`PlatformCommandRejectedError` around the `get_device_resource` call, falling back to "unresolved" (Phase 9's existing behavior) rather than raising a 500 or blocking the Pub/Sub ack. | Code review (`app/platform/android/router.py`) — the unconfigured-settings path is exercised indirectly by every *other* webhook test in `tests/test_android_webhook.py` (none set Android credentials), confirming the pre-existing early-return path this phase reuses is unaffected | PASS |
| **The real provider-minted enrollment secret is never persisted or logged** | `ProviderEnrollmentToken.value` (Android's real QR/enrollment-token secret) flows straight through `create_token`'s return value to the one-time API response (`CreateEnrollmentTokenResponse.provider_enrollment_value`) and is never written to any column — only the non-secret `reference` (the credential's own resource *name*, not its value) is persisted, matching this codebase's existing raw-token/device-secret treatment. | Code review (`app/enrollment/service.py::create_token`, `app/enrollment/models.py::EnrollmentToken`) — no dedicated test asserts "field X is absent from the database row," see §4 | PENDING (reasoning only) |
| **provision_via_provider cannot be requested without a target platform** | Rejected 400 `PROVIDER_PROVISIONING_REQUIRES_PLATFORM` — the real provider mechanism is platform-specific and `PlatformProviderRegistry.get_provider` requires a `DevicePlatform` to route on. | `tests/test_android_enrollment_provisioning.py::test_provision_via_provider_requires_allowed_platform` — **written, not yet run** | PENDING |
| **Apple provisioning fails loudly, never fakes success** | `AppleManagementProvider.enroll_device` raises `PlatformProviderUnavailableError` (503) with a message naming the real, unimplemented dependency (ABM server-to-server credentials) — propagates uncaught from `create_token`, so a caller requesting Apple provisioning gets a clear, honest failure, never a token that looks provisioned but isn't. | `tests/test_android_enrollment_provisioning.py::test_apple_provider_enroll_device_documents_unavailable` — **written, not yet run** | PENDING |
| **Enrollment-token admin permissions are unchanged, not weakened** | `provision_via_provider` is gated by the same existing `enrollment_token.create` permission — no new, more permissive path was added; the extra behavior is a detail of the same already-permissioned action. | Code review (`app/enrollment/router.py` — no new `Depends` added to `create_enrollment_token`) | PASS |

## 4. Discovered & Resolved Issues (during this phase's implementation)

1. **Initial design assumed Android Pub/Sub notifications carry `policyName`
   inline**, which would have let auto-enrollment resolve organization
   identity without any extra API call. Caught during design, before
   writing the webhook integration: this codebase's own existing
   `COMMAND`-notification handling already documents that Android's Pub/Sub
   notifications carry only a resource *name*, never the full resource —
   claiming otherwise would have been exactly the kind of undocumented-API-
   behavior invention this project's conventions explicitly forbid.
   Redesigned around a real, confidently-known mechanism instead: a
   follow-up `enterprises.devices.get` call reading `enrollmentTokenName`,
   a genuinely documented field, and resolving via the enrollment token's
   own resource name (`provider_enrollment_reference`) rather than a
   policy name. This also turned out simpler — no separate policy-name
   column was needed at all, only the credential's own reference.
2. **A stray `E501` line-length violation** was introduced in
   `app/enrollment/router.py` (the `model_copy(update={...})` call) and in
   two new test files (long parameter lists) — caught by ruff before
   considering the phase complete, all three fixed by reformatting.

## 5. Known Gaps / Deferred

- **No pytest run has happened for either new Phase 11 test file** — every
  PENDING row above reflects implemented-and-reviewed behavior, not
  confirmed behavior. This is the top-priority gap to close.
- **No dedicated test asserts the provider-minted secret is absent from
  the database** — verified by code review only (§3, "real provider-minted
  enrollment secret" row).
- **Frontend has no UI for `provision_via_provider`, the returned
  QR/enrollment value, or the provider-link fields now returned by
  `GET /devices/{id}/enrollment`** — Phase 5's enrollment page is
  unchanged. A real gap, not a considered non-goal (unlike Apple ADE,
  §4/§6 below).
- **Apple real Automated Device Enrollment (ADE) is not implemented** —
  a deliberate, documented boundary (`docs/architecture/phase-11.md` §4),
  not a partial or half-verified attempt. `AppleManagementProvider.enroll_device`
  always raises 503 for `provision_via_provider` requests targeting
  IOS/IPADOS; Apple devices are only linkable via Path A
  (device-supplied UDID) or Phase 10's manual admin API.
- **No real Google Cloud service-account credentials, real Android
  enterprise, or real device exist in this environment** — same
  limitation every prior real-provider phase documents. Every Phase 11
  Android test runs against `httpx.MockTransport`, never a real network
  call.
- **No reconciliation job was built** — evaluated and deliberately
  skipped; see `docs/architecture/phase-11.md` §9 for why the drift
  scenario a reconciliation sweep would address does not arise in this
  design (both enrollment paths create the `ProviderDeviceLink`
  atomically with the `Device`, inside one request/one transaction).
- Every other gap already documented in
  `docs/security/phase-6-security-review.md` §4,
  `docs/security/phase-8-security-review.md` §4,
  `docs/security/phase-9-security-review.md` §4, and
  `docs/security/phase-10-security-review.md` §4 (no live Postgres, no
  real credentials, no Docker) is unchanged and still applies.
