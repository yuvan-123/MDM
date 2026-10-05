# Apple MDM Capabilities — Phase 6 Verification

Companion to `docs/android/enterprise-capabilities.md`, same rigor and same
requirement: every capability claim traced to a specific current source,
never assumed to mirror Android's. Sources pulled August 2026 from Apple's
own canonical machine-readable MDM command schema
(`apple/device-management` on GitHub) and developer.apple.com.

## Protocol shape

Apple MDM is a two-channel protocol, structurally different from Android's
single authenticated REST API:

1. **Wake-up**: the server sends a silent Apple Push Notification service
   (APNs) push — no command payload, just a signal — to the device's push
   token.
2. **Check-in**: the device independently opens an HTTPS connection back
   to the server's own `/mdm` command-queue endpoint, long-polls for the
   next queued command, executes it, and POSTs a plist-encoded `Status`
   response (`Acknowledged` / `NotNow` / `Error` / `CommandFormatError`).

**Channel 2 — the HTTPS command-queue endpoint the device itself calls —
does not exist in this codebase.** This is genuinely missing
infrastructure, not a configuration gap: building it means standing up a
new authenticated HTTPS surface, a per-device command queue, and plist
request/response handling, none of which exist yet. Per this phase's
explicit instruction ("implement the provider interface and clearly
document the missing infrastructure rather than faking successful
commands"), `app/platform/apple/provider.py` implements channel 1 only
(constructing a correctly-shaped command and sending the APNs wake-up
push) and raises `PlatformProviderUnavailableError` for the rest.

## Auth model

APNs provider authentication uses a token-based ES256 JWT: header `kid`
(APNs key ID), claims `iss` (Apple Developer team ID) and `iat`, signed
with a `.p8` private key, valid up to one hour. Sent as
`authorization: bearer <token>` on every push request to
`https://api.push.apple.com/3/device/{push_token}`, alongside an
`apns-topic` header identifying the MDM push topic and
`apns-push-type: mdm`.

## Capability matrix

| Command | Status | Mechanism | Notes / Citation |
|---|---|---|---|
| **LOCK** | Supported (schema only — see Protocol shape) | `DeviceLock` command | Optional `Message`, `PhoneNumber` (iOS 7.0+), `PIN` (macOS 10.8+ only). [`device.lock.yaml`](https://github.com/apple/device-management/blob/release/mdm/commands/device.lock.yaml) |
| **WIPE** | Supported (schema only) | `EraseDevice` command | Optional `PreserveDataPlan` (iOS 11+), `DisallowProximitySetup` (iOS 11.3+), `PIN` (macOS 10.8+), `ObliterationBehavior` (macOS 12+), `ReturnToService` (iOS/tvOS/visionOS 17+/18+/26+). [`device.erase.yaml`](https://github.com/apple/device-management/blob/release/mdm/commands/device.erase.yaml) |
| **REFRESH_INFO** | Supported (schema only) | `DeviceInformation` query command | [Commands and queries](https://developer.apple.com/documentation/devicemanagement/commands-and-queries) |
| **REBOOT** | **Not Supported** | — | Apple's published MDM command schema has **no iOS/iPadOS REBOOT-equivalent command**. macOS has a separate `RestartDevice` command, out of scope — this codebase only enrolls `ANDROID`/`IOS`/`IPADOS` (`app/devices/enums.py::DevicePlatform`). Declared unsupported in `AppleManagementProvider.supported_capabilities` rather than assumed to mirror Android — this is the concrete example of "do not assume every platform supports every command" this phase asked for. |
| **POLICY_SYNC** | **Not Verified — left Not Supported** | `InstallProfile` command (believed) | Apple's real mechanism for pushing configuration is `InstallProfile`, but its full profile-payload schema (a signed `.mobileconfig`) was not part of this research pass. Declaring it "supported" without having verified the payload shape would violate "do not invent undocumented API behavior" — left unsupported and documented as a gap for a future phase to close properly, not guessed at here. |

## Command response semantics (for context)

Device responses use a `Status` field: `Acknowledged` (executed
successfully), `NotNow` (the device acknowledges receipt but cannot
execute yet — commonly because it's passcode-locked — and will
independently reconnect later), `Error` / `CommandFormatError` (with an
`ErrorChain` array of `ErrorCode`/`ErrorDomain`/`LocalizedDescription`).
None of these are ever observed by this codebase today, since channel 2
does not exist — documented for completeness and for whichever future
phase builds the command-queue endpoint.

## What was NOT verified

- **Live device behavior.** No real device, no Apple Business Manager
  enrollment, no APNs certificate/key exists in this environment. Every
  provider method is unit-tested only against a mocked HTTP transport
  (`tests/test_apple_provider.py`) — channel 1 (the wake-up push) shape
  only, never a live push, never a real device response.
- **Per-device push token / MDM PushMagic storage.** Both are only issued
  during genuine Apple MDM enrollment, which this codebase's synthetic,
  backend-only Phase 5 enrollment flow never performs — no schema field
  exists for either today.
- **`InstallProfile` payload shape** (see POLICY_SYNC row above).
- **macOS-specific commands** (`RestartDevice`, etc.) — out of scope;
  `DevicePlatform` has no `MACOS` value.

## Sources

- [Erase Device | Apple Developer Documentation](https://developer.apple.com/documentation/devicemanagement/erase-device-command)
- [Device Lock | Apple Developer Documentation](https://developer.apple.com/documentation/devicemanagement/device-lock-command)
- [Commands and queries | Apple Developer Documentation](https://developer.apple.com/documentation/devicemanagement/commands-and-queries)
- [apple/device-management (GitHub)](https://github.com/apple/device-management)
- [device.lock.yaml](https://github.com/apple/device-management/blob/release/mdm/commands/device.lock.yaml)
- [device.erase.yaml](https://github.com/apple/device-management/blob/release/mdm/commands/device.erase.yaml)
- [Handling NotNow status responses](https://developer.apple.com/documentation/devicemanagement/handling-notnow-status-responses)
