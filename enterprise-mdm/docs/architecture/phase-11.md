# Phase 11 — Real Device Enrollment & Platform Activation

Phase 5 built a genuine enrollment state machine (token issuance, device
self-registration, approve/reject/revoke) — but entirely backend-only:
`Device` identity there is a random secret + SHA-256 hash, never anything
a real Android or Apple device's own management stack produces. Phase 10
then built the *administration* of `ProviderDeviceLink` (the mapping
between that real provider identity and an internal `Device`), but only
as a manual admin CRUD operation — nothing created a link automatically.

Phase 11 closes that gap on both ends: it extends Phase 5's enrollment
token to optionally mint a **real** (or mock, depending on deployment
mode) provider-side enrollment credential, and it makes
`ProviderDeviceLink` creation automatic — both when a device supplies its
own provider identity at registration time, and, for Android
specifically, when a real device that never talked to this backend at
all checks in for the first time. It does not touch Phase 5's token
lifecycle (expiry, reuse limits, revocation semantics), Phase 6-9's
command dispatch, or Phase 10's admin CRUD/retry endpoints — those are
extended, never replaced.

**Verification status**: `python -c "import app.main"` succeeds, ruff and
`mypy --strict` are clean against every file this phase touches (see
`docs/security/phase-11-security-review.md` §4 for the exact commands and
results). Targeted tests are written but this document does not claim a
pytest run happened before it was written — check the security review for
current status.

## 1. Two independent enrollment-identity mechanisms

Phase 11 builds two genuinely different paths to the same outcome
(a `ProviderDeviceLink` existing with no admin database write), because
they serve two different real-world scenarios:

**A. Device-supplied identity at registration** (`POST /enrollment/register`,
any provider): the device already knows its own provider-native
identifier (Android's device resource id, or Apple's UDID) and supplies
it directly alongside this codebase's own enrollment token. Works for
every provider uniformly, including Apple, which has no automated
zero-touch mechanism built here (§4 below).

**B. Provider-reported check-in with no prior contact** (Android only):
a real Android device never talks to this backend at all — it provisions
itself directly against Google's infrastructure using a QR code / NFC
payload from a real Android Management API enrollment token, and this
backend only learns about it later via the Phase 9 Pub/Sub webhook. Since
the device never called our API, there is no `provider_device_id` for it
to hand us — the *provider* has to tell us, and the only thing a
first-time check-in carries that we can trace back to an organization is
which enrollment token it used.

## 2. Path A — provider identity at registration

`RegisterDeviceRequest` gains two optional fields:
`provider_device_id` (any provider) and `apple_push_token` (Apple only).
`EnrollmentService.register_device`:

- Derives the expected provider from `platform`
  (`app/platform/base/provider_names.py::platform_to_provider_name` —
  ANDROID -> "android", IOS/IPADOS -> "apple", OTHER -> no provider
  exists, so `provider_device_id` is rejected 400
  `PROVIDER_LINKING_NOT_SUPPORTED`). `apple_push_token` supplied for a
  non-Apple platform is rejected 400 `APPLE_PUSH_TOKEN_NOT_APPLICABLE`.
- Rejects a `provider_device_id` already linked in this organization
  **before** creating a Device row — 409 `DEVICE_ALREADY_ENROLLED`, no
  duplicate device, and a `SecurityEventType.DEVICE_ENROLLMENT_FAILED`
  entry is logged (§7).
- On success, creates the `ProviderDeviceLink`
  (`link_source=PROVIDER_ENROLLED`) in the same request as the `Device`
  and `DeviceEnrollment` rows — `RegisterDeviceResponse.provider_link_created`
  tells the caller whether this happened.

## 3. Path B — Android webhook auto-enrollment

The real mechanism, end to end:

1. **Token creation**: an admin creates an `EnrollmentToken` with
   `allowed_platform=ANDROID` and `provision_via_provider=true`.
   `EnrollmentService.create_token` asks
   `PlatformProviderRegistry.get_provider(ANDROID)` for whatever provider
   currently serves that platform (Mock in dev/test, real
   `AndroidManagementProvider` if `platform_provider_mode="real"` and
   credentials are configured) and calls its new `enroll_device`.
2. **`AndroidManagementProvider.enroll_device`** calls
   `AndroidApiClient.create_enrollment_token` —
   `enterprises.enrollmentTokens.create`, the real Android Management API
   mechanism for minting a QR/NFC-provisionable credential bound to a
   policy. The response's `value` (the secret, shown to the admin once,
   never persisted — same treatment as this codebase's own raw enrollment
   token) and `name` (the credential's own non-secret resource name, e.g.
   `enterprises/{id}/enrollmentTokens/{tokenId}`) come back as a
   `ProviderEnrollmentToken`. **Only `reference` (that `name`) is
   persisted**, on `EnrollmentToken.provider_enrollment_reference` — this
   is the join key path B resolves through later.
3. **The physical device provisions itself directly against Google**,
   entirely bypassing this backend, using the QR/NFC payload the admin
   handed it.
4. **First check-in**: Android's Pub/Sub notification arrives at the
   existing Phase 9 webhook (`app/platform/android/router.py`) carrying
   only the device's resource *name* — Android's own notifications never
   embed the full resource (this router's pre-existing `COMMAND`-mapping
   comment already documents this same limitation). `get_by_provider_device_id_unscoped`
   finds no link (this device has never been seen). Previously, Phase 9
   stopped there and recorded the event unresolved forever.
5. **Phase 11's new step**: exactly once per genuinely unknown device
   (never on an already-linked device's routine check-ins),
   `_try_auto_enroll_android_device` makes one real follow-up API call —
   `AndroidApiClient.get_device_resource` (`enterprises.devices.get`) —
   to fetch the full Device resource, which includes `enrollmentTokenName`:
   the exact resource name of the enrollment token this device was
   provisioned with. This is the real, documented field that closes the
   loop back to step 2's `reference`.
6. **`EnrollmentService.resolve_provider_checkin`** looks up the
   `EnrollmentToken` by that reference
   (`EnrollmentTokenRepository.get_by_provider_enrollment_reference_unscoped`
   — necessarily cross-tenant, mirroring
   `ProviderDeviceLinkRepository.get_by_provider_device_id_unscoped`'s
   precedent, safe because a provider's own resource name is globally
   unique by construction). If the token is still `ACTIVE` (the same
   `_effective_token_status` lazy-expiry check `register_device` already
   applies — an expired/revoked/exhausted token yields no auto-enrollment,
   logged as `DEVICE_ENROLLMENT_FAILED`), it auto-creates the same
   `Device`/`DeviceEnrollment`/`ProviderDeviceLink` triad `register_device`
   creates — honoring the token's own `require_approval`
   (PENDING vs. ENROLLED) and `max_uses` bookkeeping identically.

The auto-enrolled `Device` gets no `device_secret_hash` — it is managed
entirely through the real provider channel (Pub/Sub check-ins), never
this codebase's own heartbeat credential; there is no "device calls us
directly" step in this flow for a bearer secret to protect.

## 4. Apple enrollment — documented boundary, not a fake mechanism

Real Apple zero-touch enrollment (Automated Device Enrollment via Apple
Business Manager) requires server-to-server ABM API access with its own
OAuth client credentials — categorically different from the APNs push
certificate `AppleManagementProvider` already uses for command dispatch,
and no such credentials exist anywhere in this environment. Building that
integration is out of Phase 11 scope. `AppleManagementProvider.enroll_device`
raises `PlatformProviderUnavailableError` with a message explaining
exactly this and pointing at Path A (§2) as the real alternative — the
same honest "unavailable" pattern every other real-provider gap in this
codebase already uses, never a bare `NotImplementedError` and never a
faked success. `provision_via_provider=true` with `allowed_platform=IOS`
or `IPADOS` therefore always fails 503 today — by design, not by bug.

Apple devices are still fully linkable through Path A
(`provider_device_id` = UDID, `apple_push_token` optional, both supplied
at registration) or through Phase 10's manual admin API. Phase 10's
UDID/push-token separation (`ProviderDeviceLink.apple_push_token` as a
column distinct from `provider_device_id`) is unchanged and fully
respected by Path A.

## 5. Enrollment revocation cleans up the provider link

`EnrollmentService.revoke_enrollment` now also deletes any
`ProviderDeviceLink` for that device
(`ProviderDeviceLinkRepository.get_any_link_for_device`, new — the same
org-scoped, no-provider-name-needed lookup used by the enrollment-detail
endpoint, §6) before logging `PROVIDER_DEVICE_LINK_DELETED`. The `Device`
row itself, and its full history (commands, audit trail, enrollment
record), is preserved — only the provider association is removed,
identical in effect to what Phase 10's manual unlink already does. This
prevents a revoked, no-longer-legitimate device from silently absorbing a
future physical device's re-enrollment attempt under the same provider
identifier.

## 6. Enrollment status visibility

`GET /devices/{id}/enrollment` now also returns `provider_name` and
`provider_device_id` when a link exists (populated by the router via
`get_any_link_for_device`, not stored on `DeviceEnrollment` itself — no
schema duplication).

## 7. Security event activated

`SecurityEventType.DEVICE_ENROLLMENT_FAILED` has existed since Phase 4
with no code path ever raising it. Phase 11 is the first to: a duplicate
`provider_device_id` at registration, and a check-in against an
inactive/unknown enrollment token during auto-enrollment, both log it via
`AuditRepository.log_security_event` (`MEDIUM` severity, no secrets in
`details`).

## 8. Database migration

`migrations/versions/0010_phase11_provider_enrollment.py` — additive
only, does not touch 0007/0008/0009. Adds to `enrollment_tokens`:
`provision_via_provider` (bool, default false), `provider_enrollment_reference`
(indexed — the resolution key for Path B), `provider_enrollment_expires_at`.
No new `audit_event_type` or `security_event_type` values needed — every
event this phase logs already existed (§7).

## 9. What's genuinely done vs. outstanding

**Done, real-shaped, unit-tested against mocked transports:** the full
Path A flow for every provider; the full Path B flow including the real
`enrollmentTokens.create` and `devices.get` request/response shapes;
token-level validation (platform-gating, duplicate rejection, revoked/
expired/exhausted token rejection); revocation-time link cleanup;
enrollment-detail provider visibility.

**Blocked on infrastructure this environment cannot provide, not on
anything left unimplemented in this codebase:** everything in §3 beyond
the two real API calls (no real Google Cloud service-account credentials,
no real Android enterprise, no real device — same limitation
`docs/architecture/phase-9.md` and `phase-10.md` already document, now
applying to enrollment specifically); Apple ADE (§4) is a deliberate,
documented non-goal, not a partial/broken attempt.

**Not built:** a background `ENROLLMENT_RECONCILIATION` job — evaluated
and deliberately skipped. Provider/internal enrollment state cannot drift
in this design the way Phase 7's command-dispatch reconciliation problem
required a sweep for: Path A's link is created atomically with the
device in the same request, and Path B's link is created atomically with
the device inside `resolve_provider_checkin`'s own transaction — there is
no window where "provider says enrolled, internal MDM says pending" can
occur that isn't already covered by the ordinary
PENDING→ENROLLED approval workflow Phase 5 built. Frontend enrollment UI
was not extended this phase (still Phase 5's token-only page) — a real
gap, not evaluated as unnecessary; see the security review's known-gaps
list.
