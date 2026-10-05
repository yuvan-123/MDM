# Phase 9 — Real Provider Integration Security Review

## 1. Scope

Real Android/Apple provider webhooks, token caching, provider-device
identity resolution wired into dispatch, and provider health endpoints
introduced in Phase 9. Builds on `docs/security/phase-8-security-review.md`
and `docs/security/phase-6-security-review.md` — this review focuses on
what's new. Full design rationale in `docs/architecture/phase-9.md`.

## 2. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **Unauthenticated webhook rejected** | `POST /webhooks/android/pubsub` requires a shared verification token compared with `hmac.compare_digest` (constant-time — a naive `==` would be a timing side channel); unset configuration means every request is rejected, never "accept anything." | `tests/test_android_webhook.py::test_pubsub_push_rejects_missing_token`, `test_pubsub_push_rejects_wrong_token` | PASS |
| **Malformed external input rejected safely** | Both webhooks parse untrusted bytes (Pub/Sub JSON/base64 envelope; Apple XML plist) inside a narrow `try`/`except ValueError`, mapped to `400`, never propagating an unhandled exception (`500`) or crashing the worker/request. | `tests/test_android_webhook.py::test_pubsub_push_rejects_malformed_envelope`, `tests/test_apple_checkin.py::test_parse_plist_rejects_garbage` | PASS |
| **Cross-tenant device resolution fails safe, never guesses** | `ProviderDeviceLinkRepository.get_by_provider_device_id_unscoped` (the one legitimate cross-tenant query Phase 9 adds) raises `AmbiguousProviderDeviceLinkError` rather than returning an arbitrary match when more than one organization has (through operator error) linked the same identifier; the webhook catches this, logs it, and records the event unresolved rather than attributing it to the wrong tenant. | Code review (`app/repositories/provider_device_link_repository.py`) — this specific failure mode requires a deliberately-misconfigured multi-org collision to trigger, not exercised by an automated test in this pass | PASS (reasoning verified, not exercised) |
| **Apple check-in requires a pre-established link — never auto-trusts an inbound UDID** | An unlinked UDID is rejected (`404 APPLE_DEVICE_NOT_LINKED`) rather than the link being auto-created from an unauthenticated Authenticate/TokenUpdate message, which would let an arbitrary caller claim any UDID as belonging to any organization's device. | `tests/test_apple_checkin.py::test_checkin_unlinked_udid_is_404` | PASS |
| **Provider event -> command completion still SENT-only and org-scoped** | Both new inbound paths (Android `COMMAND` notifications, Apple `Status` responses) route through the *existing, unmodified* `CommandService.apply_provider_event` (Phase 8) — no new code path writes to `DeviceCommand`. The guarantee that a command can only move `SENT -> terminal`, scoped to the resolved organization, is unchanged by this phase. | `tests/test_apple_checkin.py::test_connect_status_acknowledged_completes_the_command` | PASS |
| **`NotNow` cannot be coerced into a state change** | `map_status_to_event_type("NotNow")` returns `None`, and the router only calls `ingest_event` when a mapped event type exists — a device stalling with repeated `NotNow` responses can never accidentally advance or fail a command. | `tests/test_apple_checkin.py::test_connect_status_not_now_leaves_command_unchanged` | PASS |
| **No provider credentials logged or returned** | Health endpoints (`/webhooks/android/health`, `/mdm/apple/health`) return only `{configured, ok, error}` — `error` is `AppError.message` text (e.g. "HTTP 503"), never the service-account JSON, private key contents, or a minted token. `AndroidApiClient`/`ApnsApiClient` never write credential material to structured logs (only `logger.warning`/`error` calls elsewhere log identifiers like `provider_device_id`, never key material). | Code review (`app/platform/android/client.py`, `app/platform/apple/client.py`, both router health handlers) | PASS |
| **Provider health endpoints are platform-scoped, not org-bypassable** | Both gated by `require_permission(p.AUDIT_READ)` — a *platform*-level permission check (`CurrentUser.has_permission`, Phase 2's platform-role model), not the header-based org-context check every other Phase 4+ endpoint uses. An org-scoped `ORGANIZATION_ADMIN` grant (the common case) does not satisfy it — only a genuine platform-level role (SUPER_ADMIN) does, correctly reflecting that Android/Apple credentials are deployment-wide, not per-organization. | `tests/test_android_webhook.py::test_android_health_requires_platform_permission`, `tests/test_apple_checkin.py::test_apple_health_requires_platform_permission` | PASS |
| **Token caching cannot leak across different credentials** | Both process-wide caches are keyed by the credential identity itself (Android: `client_email` parsed from the service-account JSON; Apple: `(key_id, team_id)`) — a `Settings` override with different credentials (every test) can never be served a token minted for a different account. | `tests/test_android_provider.py::test_access_token_is_cached_across_calls` (plus the autouse cache-clearing fixture proving isolation is even necessary) | PASS |
| **Transient provider failures don't get treated as permanent (or vice versa)** | Both clients classify `429`/`5xx` as `PlatformProviderUnavailableError` (retryable — already in `app/jobs/retry.py::is_transient`'s transient set) and every other `4xx` as `PlatformCommandRejectedError` (permanent) — an operator misconfiguration or invalid request is never silently retried forever, and a rate limit or outage is never treated as a permanent, unretryable failure. | `tests/test_android_provider.py::test_rate_limit_response_is_classified_as_transient`, `test_server_error_response_is_classified_as_transient` (+ Apple equivalents) | PASS |
| **CSRF exemptions scoped precisely** | New `EXEMPT_PATHS`/`EXEMPT_PATH_PATTERNS` entries cover exactly the new webhook paths, following the exact precedent and rationale already established for `/enrollment/register` and device heartbeats — CSRF only ever fires when an access cookie is present in the first place, so these entries only matter for the edge case of an admin testing the endpoint from their own authenticated browser tab. | Code review (`app/auth/middleware.py`) | PASS |

## 3. Discovered & Resolved Issues (during this phase's implementation)

1. **Moving HTTP transport out of `provider.py` into new `client.py`
   modules broke both providers' existing mock-transport tests**, which
   monkeypatched `httpx.AsyncClient` at the old module path. Caught before
   considering the refactor complete — both test files updated to patch
   the new location, matching the actual code path.
2. **Introducing a process-wide token cache created cross-test
   contamination risk** — a token cached by one test would silently make
   a later test's own mock-transport token-endpoint handler unreachable,
   which could mask a real regression in the token-minting path without
   any test actually failing. Caught during implementation, before relying
   on the caching tests' results — both provider test files gained an
   `autouse` fixture clearing the relevant module's cache before every
   test.
3. **Apple's UDID (inbound identity) and push token (outbound identity)
   turned out to be genuinely different concepts** that Phase 6's single
   `provider_device_id` column conflates. Recognized during design, before
   writing the check-in endpoint against a wrong assumption — resolved by
   scoping Phase 9 to only the inbound (UDID) direction and explicitly
   documenting the resulting internal inconsistency rather than either
   guessing at a fix or silently shipping something subtly wrong (see
   `docs/architecture/phase-9.md` §3/§6).

## 4. Known Gaps / Deferred

- **OIDC-based Pub/Sub authentication** was not implemented — the shared
  verification-token mechanism is real and Google-documented, but weaker
  than OIDC; deferred rather than half-verified against infrastructure
  this environment cannot exercise.
- **The Apple UDID/push-token column conflation** (§3 above) is
  unresolved — a real deployment would need either a second column or a
  protocol-aware resolution helper before outbound Apple dispatch could
  ever work correctly alongside inbound check-in resolution.
- **No automated test exercises the actual multi-organization ambiguity
  path** in `get_by_provider_device_id_unscoped` (verified by code review
  only) — reproducing it requires deliberately creating a colliding link
  across two organizations, which is a real but narrow scenario.
- **Nothing in this codebase can create a real `ProviderDeviceLink` row**
  for either platform — both require real platform enrollment (Android
  Enterprise / Apple ADE) that no phase, including this one, implements.
  Every webhook/check-in test in this phase pre-creates the link directly
  against the database, exactly as a real deployment could not.
- Every other gap already documented in
  `docs/security/phase-6-security-review.md` §4 and
  `docs/security/phase-8-security-review.md` §4 (no live Postgres, no real
  credentials, no Docker) is unchanged and still applies.
