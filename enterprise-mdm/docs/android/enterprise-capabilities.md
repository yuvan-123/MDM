# Android Enterprise Capabilities — Phase 6 Verification

Mandated by `docs/architecture/android-enterprise.md` §4: before any Android
Enterprise code is written, every feature requested must be mapped to
Supported / Partially Supported / Not Supported / Requires Specific
Management Mode, cited to current official documentation. Sources pulled
August 2026 from developers.google.com — not from memory, not from older
tutorials.

## Auth model

Android Management API authenticates as a Google Cloud service account.
The provider (`app/platform/android/provider.py`) exchanges a self-signed
RS256 JWT for a bearer access token via the standard RFC 7523 JWT-Bearer
flow against `https://oauth2.googleapis.com/token`, scope
`https://www.googleapis.com/auth/androidmanagement`. All REST calls target
`https://androidmanagement.googleapis.com/v1`.

## Capability matrix

| Command | Status | Mechanism | Notes / Citation |
|---|---|---|---|
| **LOCK** | Supported | `enterprises.devices.issueCommand(type=LOCK)` | No parameters. [Method: enterprises.devices.issueCommand](https://developers.google.com/android/management/reference/rest/v1/enterprises.devices/issueCommand) |
| **REBOOT** | Supported | `enterprises.devices.issueCommand(type=REBOOT)` | No parameters. Same reference. |
| **WIPE** | Supported | `enterprises.devices.issueCommand(type=WIPE, wipeParams={...})` | Factory-resets a company-owned device, deletes the work profile on a personally-owned one; deletes the device record on completion. A separate, blunter `enterprises.devices.delete` (with `wipeDataFlags`) also exists — deletes the device record immediately and best-effort wipes, not guaranteed if the device is offline. This implementation uses the `issueCommand` `WIPE` type, not `delete`, to keep the device record intact until the platform confirms the action (see [Method: enterprises.devices.delete](https://developers.google.com/android/management/reference/rest/v1/enterprises.devices/delete) for the distinction). |
| **REFRESH_INFO** | Supported | `enterprises.devices.issueCommand(type=REQUEST_DEVICE_INFO)` | Actively requests fresh device-reported info, distinct from `enterprises.devices.get` (a synchronous read of Google's own possibly-stale cached resource). Chosen so every Android command type is consistently asynchronous. |
| **POLICY_SYNC** | Partially Supported | `enterprises.devices.patch` re-asserting `policyName` | The Android Management API has **no distinct "sync now" push command** — a device applies its assigned policy automatically on its next check-in once `policyName` is set on its Device resource. Re-PATCHing the same `policyName` is the closest available action and is best-effort reconciliation, not a guaranteed immediate sync. |

## Command dispatch model

`enterprises.devices.issueCommand` returns a long-running `Operation`
resource, not a synchronous result — the caller must poll
`operations.get` for eventual completion. This provider reports every
command as `SENT` (never `COMPLETED`) with the `Operation` name as
`provider_command_id`, and does not implement operation polling — that
would require a scheduled background job, which this codebase does not
have (see `docs/architecture/device-commands.md` "Known Gaps").

## What was NOT verified

- **Live device behavior.** No real Android Enterprise account, service
  account, or enrolled device exists in this development environment
  (confirmed: no Docker, `docker --version` fails). Every provider method
  is unit-tested only against a mocked HTTP transport
  (`tests/test_android_provider.py`) — shape-correctness, not
  live-integration proof.
- **Management-mode-specific restrictions.** Some commands may behave
  differently under work-profile (BYOD) vs. fully-managed (company-owned)
  modes — this matrix reflects the general REST API surface, not a
  mode-by-mode breakdown. `docs/architecture/android-enterprise.md` §7
  already scopes V1 to company-owned, fully-managed devices first.
- **Quota and rate-limit behavior** for `issueCommand` at scale — not
  relevant to this phase's synchronous, one-command-per-request design.

## Sources

- [Android Management API overview](https://developers.google.com/android/management/introduction)
- [Method: enterprises.devices.issueCommand](https://developers.google.com/android/management/reference/rest/v1/enterprises.devices/issueCommand)
- [Method: enterprises.devices.delete](https://developers.google.com/android/management/reference/rest/v1/enterprises.devices/delete)
- [WipeDataFlag](https://developers.google.com/android/management/reference/rest/v1/WipeDataFlag)
- [Wipe and deprovision a device](https://developers.google.com/android/management/deprovision-device)
