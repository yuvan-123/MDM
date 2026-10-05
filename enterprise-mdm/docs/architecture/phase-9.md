# Phase 9 — Real Android / Apple Provider Integration

Phase 6 already built real (not mock) Android OAuth2/Android Management
API and Apple APNs integration — genuinely correct request/response shapes
against each platform's current documented API, never live-verified (no
credentials, no Docker, no real devices in this environment). Phase 9
closes five specific gaps on top of that foundation: token caching, a
dedicated transport layer, real inbound webhook endpoints, tenant-safe
device identity resolution actually wired into dispatch, and operational
health visibility. It does **not** rebuild the provider abstraction, does
not add Celery, and does not claim any real device was ever contacted —
every claim below is either "implemented and unit-tested against a mocked
transport" or explicitly marked as blocked on infrastructure this
environment doesn't have.

## 1. What changed

- **`ProviderDeviceLink` is now wired into outbound dispatch.**
  Phase 8 built the table only for the *inbound* direction (an event
  resolving to a device). `CommandService._dispatch` now resolves it too,
  populating `ProviderDeviceRef.provider_device_id` before calling a
  provider — closing a real, previously-inert loop. See §5.
- **Android**: `app/platform/android/client.py::AndroidApiClient` — HTTP
  transport, OAuth2 token caching, and error classification extracted out
  of `AndroidManagementProvider` (which is now domain-verb mapping only).
  A real Pub/Sub push webhook (`app/platform/android/router.py`) actually
  receives and processes Android Management API notifications.
- **Apple**: `app/platform/apple/client.py::ApnsApiClient` — the same
  extraction for APNs transport + provider-token caching.
  `app/platform/apple/checkin.py`/`router.py` build channel 2 (the
  device-initiated command-queue connection Phase 6 explicitly deferred)
  — check-in message handling and command dequeue/Status processing,
  reusing `DeviceCommand` directly as the queue.
- **Operational health**: `GET /webhooks/android/health`,
  `GET /mdm/apple/health` — platform-level (not org-scoped, since
  credentials are deployment-wide), gated by a platform permission.

## 2. Android Pub/Sub webhook (`app/platform/android/webhook.py` + `router.py`)

Google Pub/Sub push subscriptions are the real notification channel for
Android Management API (`docs/android/enterprise-capabilities.md`).
**Authentication**: a shared verification token
(`Settings.android_pubsub_verification_token`) appended to the push
subscription's own endpoint URL as a query parameter, compared with
constant-time equality (`hmac.compare_digest`) — this is Google's own
documented mechanism for authenticating a push endpoint beyond bare HTTPS.
OIDC bearer-token verification (Google's other documented option) was
deliberately not implemented: verifying it means fetching and caching
Google's public JWKS at runtime, which this environment has no way to
exercise or prove correct without live network access to Google's
infrastructure. A shared token is simpler and genuinely secure when kept
secret; OIDC is left for a later phase rather than half-verified here.

**Device resolution has no organization context to start from.** Android
notifications carry no organization identifier — `Settings` has exactly
one global `android_enterprise_id` for the whole deployment (not one per
organization), so Pub/Sub notifications are enterprise-wide, not
org-scoped. `ProviderDeviceLinkRepository.get_by_provider_device_id_unscoped`
(new, Phase 9) is the one legitimate cross-tenant query this requires —
safe *because* the deployment architecture assumes exactly one Android
enterprise, so `(provider_name, provider_device_id)` is expected to be
globally unique in practice. If it somehow isn't (operator
misconfiguration, not something this codebase could cause on its own),
the method raises `AmbiguousProviderDeviceLinkError` rather than picking a
match — the webhook catches that, logs it, and records the event
unresolved rather than guessing (Phase 9's explicit "fail safely, never
choose the first matching device" rule).

**Notification-type mapping is best-effort, not verified against real
traffic** (no live enterprise exists here): `STATUS_REPORT`/
`DEVICE_REPORT_UPDATE` -> `DEVICE_CHECK_IN` (schedules compliance
evaluation, mirroring the existing heartbeat trigger); `COMMAND` ->
`COMMAND_ACKNOWLEDGED` (Android's own notification can't reliably
distinguish "acknowledged" from "genuinely completed" without a follow-up
`operations.get` call this codebase still doesn't make — mapped to the
weaker, honest claim); anything else passes through as the raw
`notificationType` string, so an unrecognized type is recorded and
correctly `IGNORED` by the existing Phase 8 handler registry, not dropped.

The webhook returns 2xx once the envelope is authenticated and parseable,
regardless of what `ingest_event`'s *processing* outcome was — a
processing failure is recorded on the row itself
(`processing_status=FAILED`), and redelivering the same message would
never fix it, so there's no reason to make Pub/Sub retry. Only
authentication failure and a malformed envelope (both things Pub/Sub
itself sent) return non-2xx.

## 3. Apple channel 2 (`app/platform/apple/checkin.py` + `router.py`)

`app/platform/apple/provider.py`'s module docstring already explained
Apple MDM's two channels: an outbound APNs wake-up (channel 1, Phase 6)
and a device-initiated HTTPS connection back to the server for the actual
command (channel 2, genuinely missing infrastructure, not just
unconfigured credentials). Phase 9 builds channel 2:

- `POST /mdm/apple/{organization_id}/checkin` — Authenticate, TokenUpdate,
  CheckOut messages. Org-scoped by **URL path segment**, not invented —
  real multi-tenant MDM servers commonly encode this in the enrollment
  profile's own `CheckInURL`/`ServerURL`, which is per-deployment (often
  per-organization) by design.
- `POST /mdm/apple/{organization_id}/connect` — command dequeue and
  Status responses. Apple's real protocol allows at most one outstanding
  command per device; this maps directly onto the existing
  `DeviceCommand.status == SENT` invariant rather than adding a second
  queue table (explicitly required: "do not create a second Apple command
  queue").

**The honest limitation**: both endpoints resolve a device via UDID
through the *existing* `ProviderDeviceLinkRepository.get_device_id`
— **nothing in this codebase populates that link for a real Apple
device.** Real Apple enrollment happens through Apple Business Manager /
Automated Device Enrollment (ADE), issuing a signed profile that ties a
UDID to a specific MDM server identity — building that is explicitly out
of Phase 9's scope (the brief: do not claim real ADE). So an unlinked UDID
correctly 404s (`APPLE_DEVICE_NOT_LINKED`) rather than being silently
accepted or the link auto-created from an unauthenticated message (which
would let an arbitrary caller claim any UDID). This is implemented in
shape and unit-tested; it is not connectable to a real device without a
later phase building real ADE-driven enrollment.

**A genuine internal inconsistency, documented rather than solved**:
`ProviderDeviceLink.provider_device_id` means the device's UDID for the
*inbound* check-in direction (this phase), but
`app/platform/apple/provider.py::_require_push_identity` still reads the
same column as the device's APNs push token for the *outbound* dispatch
direction (unchanged from Phase 6). Reconciling that would need either a
second column or a protocol-aware resolution helper — deferred, since
neither direction is live-verifiable in this environment regardless of
which identifier the column holds, and building schema to solve an
unverifiable problem was judged premature. See §6.

**Status semantics**: `Acknowledged` -> `COMMAND_COMPLETED`,
`Error`/`CommandFormatError` -> `COMMAND_FAILED`, both bridged through the
*existing* `CommandService.apply_provider_event` (Phase 8) — same
SENT-only, org-scoped, never-fakes-completion guarantees, unchanged.
`NotNow` produces no event and no state change at all — it means "ask me
again later," not a terminal outcome, matching Phase 6's command state
machine having no transition for it.

## 4. Token caching (`app/platform/android/client.py`, `app/platform/apple/client.py`)

`PlatformProviderRegistry.get_provider` constructs a brand-new provider
instance on every call (a deliberate, already-audited Phase 6 design so a
registry built from an explicit `Settings` override — as every test does
— is always the source of truth). An instance-level token cache would
therefore never actually persist across calls. Both clients instead use
**process-wide module state**, mirroring the one existing precedent for
this shape in the codebase: `app/core/redis.py::get_redis_client`'s
`global _redis_client` singleton. Keyed by the credential identity itself
(Android: `client_email`; Apple: `(key_id, team_id)`), so a credential
change can never serve a stale token minted for different credentials.

## 5. Provider-device link now reaches dispatch

`ProviderDeviceLinkRepository.get_provider_device_id` (new, reverse of
Phase 8's `get_device_id`) is called from `CommandService._dispatch`
before building `ProviderDeviceRef`, keyed on `command.provider_name`
(not just "the device's platform") so a link created for the wrong
provider can never resolve. `MockManagementProvider` — the only provider
ever selected by default — ignores `provider_device_id` entirely, so this
has zero effect on the test suite's normal behavior; it only matters once
`platform_provider_mode="real"` and a link actually exists, neither of
which is true anywhere in this environment.

## 6. What's genuinely production-ready vs. blocked

**Ready, in the sense of "correct shape, unit-tested against a mocked
transport, ready to point at real credentials":**
- Android OAuth2 JWT-Bearer auth, token caching, issueCommand/patch
  request shapes, transient/permanent error classification.
- Apple APNs token auth (ES256 JWT), token caching, wake-up push request
  shape.
- Both webhook/check-in endpoints' authentication, parsing, and
  resolution logic.

**Blocked on infrastructure this environment cannot provide**, not on
anything left unimplemented in this codebase:
- Real Google Cloud service-account credentials, a real Android
  enterprise binding, and a real Pub/Sub subscription pointed at this
  webhook.
- Real APNs certificate/key material and a real MDM push topic.
- Real Android Enterprise device enrollment (Phase 5's enrollment is
  synthetic) — without it, no `ProviderDeviceLink` row for Android could
  ever be created by anything other than a direct database write.
- Real Apple Business Manager / ADE enrollment — same limitation for
  Apple `ProviderDeviceLink` rows.
- OIDC-based Pub/Sub authentication (§2) and the
  push-token/UDID column reconciliation (§3) — both deferred, not
  attempted half-verified.

No device has ever been locked, wiped, rebooted, or otherwise controlled
by any code in this codebase, at any phase including this one. Every test
in this phase runs against `httpx.MockTransport` or a plain HTTP test
client — never a real network call.
