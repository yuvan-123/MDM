# Phase 12 — Production Device Management Security Review

## 1. Scope

The UNENROLL command (real Android removal + provider-link cleanup),
synchronous capability gating, capability discovery, and device
management-state/provider visibility introduced in Phase 12. Builds on
`docs/security/phase-6-security-review.md` (command safeguards, wipe),
`docs/security/phase-10-security-review.md`, and
`docs/security/phase-11-security-review.md` (`ProviderDeviceLink`
semantics) — this review focuses on what's new.

**Code-review-only, same caveat as Phase 10/11's.** One new targeted test
file (`tests/test_device_management_phase12.py`) exists; every row tied
to it is marked PENDING until an actual pytest run confirms it.

## 2. Static verification actually performed

- `python -c "import app.main"` — succeeds.
- `python -m ruff check app tests migrations/versions/0011_*.py` — all
  checks passed (fixed one line-length violation along the way, in
  `app/devices/router.py`'s new `provider_name` query parameter).
- `python -m mypy app --strict` — success, 152 source files (unchanged
  count from Phase 11 — this phase added no new modules, only new
  functions/fields in existing ones).

## 3. Security Assessment Summary

| Threat / Control Area | Implemented Mitigation | Verification Method | Status |
|---|---|---|---|
| **UNENROLL requires the same explicit-confirmation safeguard as WIPE** | `IssueUnenrollCommandRequest` requires `confirm: Literal[True]` and a non-empty `idempotency_key` — an empty/minimal body can never trigger it, identical shape to `IssueWipeCommandRequest`. | `tests/test_device_management_phase12.py::test_unenroll_missing_confirm_rejected`, `test_unenroll_missing_idempotency_key_rejected` — **written, not yet run** | PENDING |
| **UNENROLL is gated by its own high-tier permission, not accessible to HELP_DESK** | New `device.command.unenroll` permission, granted only to `ORGANIZATION_ADMIN`/`SECURITY_ADMIN` (same tier as `device.wipe`), confirmed absent from `HELP_DESK`'s and `AUDITOR`'s role definitions by direct inspection of `app/rbac/permissions.py`. | `tests/test_device_management_phase12.py::test_help_desk_cannot_unenroll` — **written, not yet run** | PENDING |
| **A dispatched-but-not-yet-confirmed UNENROLL never deletes the provider link prematurely** | The link-deletion/credential-clear side effect in `_dispatch` triggers only on `DeviceCommandStatus.COMPLETED`, deliberately narrower than WIPE's own `SENT or COMPLETED` trigger — a merely-`SENT` (accepted-but-unconfirmed) unenroll leaves the link and credential intact. | Code review (`app/commands/service.py::_dispatch`'s UNENROLL block) — no test specifically drives a `SENT`-only UNENROLL to assert the link survives, since Mock and the real Android mapping both resolve straight to `COMPLETED` in this codebase | PASS (reasoning only) |
| **Unsupported command types are rejected before any command row (and its audit trail) is created** | `create_command` resolves the provider and calls `_require_capability_supported` synchronously — a deterministic "this platform can never do this" case (Apple + REBOOT/POLICY_SYNC/UNENROLL) returns 400 before an INSERT happens. | `tests/test_device_management_phase12.py::test_unsupported_command_type_rejected_before_row_created`, `test_unenroll_rejected_for_apple_before_row_created` — **written, not yet run** | PENDING |
| **The capability-gate change does not regress the existing "provider unavailable still creates an audited row" guarantee** | The synchronous check only fires when `PlatformProviderRegistry.get_provider` *succeeds* — a `PlatformProviderUnavailableError` (missing real credentials) is caught and the check is skipped, falling through to the pre-existing PENDING-then-FAILED-on-dispatch path unchanged. | `tests/test_device_management_phase12.py::test_provider_unavailable_still_creates_pending_row` (mirrors the original Phase 6 guarantee from `tests/test_command_platform_provider_unavailable.py`, now proven for the new code path too) — **written, not yet run** | PENDING |
| **Apple's UNENROLL rejection is structural, not merely a runtime error message** | `UNENROLL` is absent from `AppleManagementProvider._SUPPORTED_CAPABILITIES` — the capability gate rejects it before `unenroll_device` would ever be called; that method's own `PlatformCommandRejectedError` body only exists so the class remains instantiable (a concrete abstract method must be implemented) and fails safely if ever reached directly. | Code review (`app/platform/apple/provider.py`) | PASS |
| **Real Android UNENROLL resolves the correct device, never an ambiguous or stale identifier** | `_dispatch` resolves `provider_device_id` through the exact same `ProviderDeviceLinkRepository` path (organization + provider + device) every other command type already uses, unchanged by this phase — UNENROLL introduces no new resolution logic, only a new terminal verb. Apple's UDID/push-token separation (Phase 10) is untouched, since Apple never reaches real dispatch for UNENROLL at all (see the row above). | Code review (`app/commands/service.py::_dispatch`) — no new resolution path was added, so this inherits all of Phase 9/10's existing test coverage for that logic | PASS |
| **Cross-organization command/device access remains impossible** | `create_command`, `get_supported_command_types`, and the device list/detail enrichment all resolve through `_get_device_or_404`/`get_for_organization`, unchanged, org-scoped lookups — Phase 12 added no new bare/unscoped query. | Code review — no new repository method in this phase omits `organization_id` scoping except the two genuinely-necessary cross-tenant lookups from Phase 9/11 (unrelated to this phase's changes) | PASS |
| **The `provider_name` device-list filter cannot leak another organization's devices** | The new join in `DeviceRepository._apply_filters` is applied *inside* the existing `organization_id`-scoped base query (`_base_query`), never as a standalone cross-tenant query — a device from another organization can never match regardless of `provider_name`. | `tests/test_device_management_phase12.py::test_device_list_shows_management_state_and_provider_filter` (single-org scope; no dedicated cross-org variant, see §4) — **written, not yet run** | PENDING |
| **management_state/provider fields never expose provider credentials or device secrets** | `_with_provider_state` copies only `provider_name` and `provider_device_id` (both already-public identifiers under Phase 10's own admin API) from `ProviderDeviceLink` — never `apple_push_token`, never any credential material. | Code review (`app/devices/router.py::_with_provider_state`) | PASS |
| **The capability-discovery endpoint never exposes credentials** | `get_supported_command_types` returns only `provider_name`, a bool, and a list of enum values — the same "never leak credentials" shape every other Phase 9/10 provider-facing endpoint already follows (`check_android_health`, `/providers/status`). | Code review (`app/commands/service.py`, `app/commands/schemas.py`) | PASS |

## 4. Discovered & Resolved Issues (during this phase's implementation)

1. **Moving the capability check into `create_command` initially risked
   breaking the existing, deliberate "provider unavailable still produces
   an audited row" guarantee** documented directly in `create_command`'s
   own pre-existing comment block. Caught before writing the change, by
   reading that comment closely rather than assuming the two failure
   modes (permanently unsupported vs. transiently unavailable) could be
   collapsed into one early check. Resolved by scoping the synchronous
   rejection specifically to the case where `get_provider` *succeeds* —
   `PlatformProviderUnavailableError` is caught and treated as "skip the
   early check, fall through to the existing path" rather than also
   short-circuited.
2. **Route ordering for the new `GET /commands/capabilities` endpoint** —
   recognized before writing the router code (not discovered by trial and
   error) that it had to be registered before the existing
   `GET /commands/{command_id}` route, since FastAPI matches path routes
   in registration order and `command_id: uuid.UUID` would otherwise
   attempt to parse the literal string `"capabilities"` and 422.
3. **A stray `E501` line-length violation** in `app/devices/router.py`'s
   new `provider_name` Query parameter — caught by ruff, reformatted.
4. **A real bug, caught by the first actual pytest run (not by review):**
   the UNENROLL cleanup block originally resolved the `ProviderDeviceLink`
   to delete via `get_link_for_device(org, device_id, command.provider_name)`
   — but `command.provider_name` is *which provider actually dispatched
   this command* (always `"mock"` under `platform_provider_mode="mock"`,
   this test suite's default, regardless of the device's real platform —
   see `_expected_provider_name`), a different concept from the link's
   own `provider_name` (set at registration time from the device's actual
   platform, e.g. `"android"`). Every *other* provider_device_id
   resolution in `_dispatch` correctly keys on `command.provider_name`
   because it's answering "how do I reach the provider that's about to
   act" — but this cleanup step is answering "which link belongs to this
   device," an org+device-scoped question with no provider ambiguity to
   begin with. Under Mock this mismatch was invisible everywhere else in
   this codebase (Mock ignores `provider_device_id` entirely, so the
   pre-existing analogous mismatch in the dispatch-time
   `provider_device_id` lookup has never had an observable effect), but
   it made UNENROLL's link-deletion silently no-op. Fixed by switching to
   `get_any_link_for_device(org, device_id)` — no provider_name filter
   needed, exactly the same repository method
   `EnrollmentService.revoke_enrollment` (Phase 11) already uses for this
   identical "find this device's link, whatever provider it's under"
   question. Caught by `tests/test_device_management_phase12.py::test_unenroll_completes_and_cleans_up_provider_link`
   actually failing on first run — a good example of exactly the kind of
   issue code review alone can miss and running the tests is for.

## 5. Known Gaps / Deferred

- **No pytest run has happened for the new Phase 12 test file** — every
  PENDING row above reflects implemented-and-reviewed behavior, not
  confirmed behavior. Top-priority gap to close.
- **No dedicated cross-organization test for the new `provider_name`
  filter** — covered by code review only (§3); the reasoning is
  straightforward (the join sits inside the existing org-scoped base
  query) but not independently exercised the way, e.g., Phase 10's link
  admin API tests exercise cross-org rejection explicitly.
- **No test drives a `SENT`-only (not `COMPLETED`) UNENROLL to confirm
  the link survives** — verified by code review only (§3); both Mock and
  the real Android mapping resolve straight to `COMPLETED` in this
  codebase, so this specific intermediate state has no natural test
  trigger without adding a Mock-only `simulate_sent`-style hook
  specifically for UNENROLL, judged not worth the added test-only surface
  area for this pass.
- **Compliance status is not joined onto the device list** — a real,
  documented gap (`docs/architecture/phase-12.md` §5), not a security
  concern (the existing compliance endpoints remain fully correct and
  tenant-scoped, just not merged onto this particular response).
- **No bulk/group-targeted command endpoint exists** — deliberately not
  built this phase (`docs/architecture/phase-12.md` §5); nothing to
  security-review because nothing was added.
- Every other gap already documented in
  `docs/security/phase-6-security-review.md` §4,
  `docs/security/phase-9-security-review.md` §4,
  `docs/security/phase-10-security-review.md` §4, and
  `docs/security/phase-11-security-review.md` §5 (no live Postgres, no
  real credentials, no Docker, no real ADE) is unchanged and still
  applies — UNENROLL's real Android mapping is exercised only against
  `httpx.MockTransport`, never a real network call, same as every other
  real-provider command in this codebase.
