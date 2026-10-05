# Android Enterprise Integration Architecture

## 1. Status

**No Android Enterprise integration was implemented in Phase 1.** This
document defines the abstraction the rest of the platform is built
against, and the process for verifying real capabilities before real
platform code is implemented. Phase 6 is the phase that actually did this
research and implementation (originally anticipated as Phase 5's work in
this document's early drafts, but Phase 5 shipped enrollment/policy/
compliance instead and deferred device commands to Phase 6) — see
`docs/android/enterprise-capabilities.md` for the resulting capability
matrix and `app/platform/android/provider.py` for the implementation.
**Never live-verified against a real Android Enterprise account** — no
Docker, no real service-account credentials exist in this development
environment; see `docs/security/phase-6-security-review.md` for the full
disclosure. Nothing here should be read as "already working against a
real device." Phase 9 added token caching, a dedicated transport client
(`app/platform/android/client.py`), and a real Pub/Sub push webhook
(`app/platform/android/router.py`) — see `docs/architecture/phase-9.md`;
that phase's own disclosure applies equally: correct in shape, unverified
against real traffic.

## 2. Why an Abstraction Layer

The core domain (devices, policies, applications, commands) must not import
or depend on Android-specific types, so that:
- iOS/iPadOS can be added later via a second adapter without touching core
  services, API routes, or the database schema's shape (only an adapter
  implementation and platform-specific extension tables are added).
- Android Enterprise API changes are contained to one module instead of
  scattered through the codebase.

## 3. The `PlatformManagementProvider` Interface

Reserved at `backend/app/platform/base/`. Conceptual shape (Phase 1 defines
the interface as an ABC with `NotImplementedError` bodies only — no
implementation yet):

```python
class PlatformManagementProvider(ABC):
    async def enroll_device(self, ...): ...
    async def get_device(self, ...): ...
    async def get_devices(self, ...): ...
    async def apply_policy(self, ...): ...
    async def remove_policy(self, ...): ...
    async def install_application(self, ...): ...
    async def remove_application(self, ...): ...
    async def lock_device(self, ...): ...
    async def restart_device(self, ...): ...
    async def wipe_device(self, ...): ...
    async def retire_device(self, ...): ...
    async def get_compliance_state(self, ...): ...
```

`AndroidManagementProvider` (in `platform/android/`) will implement this
against the real Android Management API once Phase 5 research is complete.
`AppleManagementProvider` (in `platform/apple/`) is a placeholder for a
future phase.

## 4. Mandatory Research Step Before Phase 5

Before any Android Enterprise code is written, Phase 5 must:

1. Pull current official documentation from Google (Android Management API
   / Android Enterprise), not from memory or older tutorials — API surface,
   auth model (service account + enterprise binding), enrollment tokens,
   supported management modes, and quota limits change over time.
2. Produce `docs/android/enterprise-capabilities.md` mapping every feature
   requested in the product spec to one of: Supported / Partially Supported
   / Not Supported / Requires Specific Management Mode, with a citation to
   the specific current doc page.
3. Only implement what is verified supported. Anything not yet verified
   stays behind a clearly labeled mock adapter (see §5) — never presented to
   an operator as functioning device control.

## 5. Mock Adapter Policy

Until real integration lands, a `MockManagementProvider` may be used in
development so the rest of the stack (device inventory UI, command queue
state machine, etc.) can be built and tested end-to-end. Requirements:

- Its class/module name and any UI badge must say "Mock" / "Simulated"
  explicitly — never silently swapped for the real provider.
- It must be selected only via explicit configuration
  (`PLATFORM_PROVIDER=mock`), never as a silent fallback when real
  credentials are missing in production.
- No API response, log line, or UI copy generated through the mock adapter
  may claim a real device action occurred (e.g. must not say "Device
  locked" — should say "Simulated: device lock command accepted").

## 6. Data Minimization Constraint

Only device attributes that map to a legitimate, documented enterprise
management purpose may be collected (see `security-architecture.md` §10 for
the explicit list of things this platform will never implement: hidden
surveillance, message interception, mic/camera capture, keylogging,
credential harvesting, undisclosed location tracking, arbitrary file
extraction). Every field added to the device model in later phases should be
traceable to a specific supported Android Enterprise API field.

## 7. Company-Owned Devices First

V1 prioritizes company-owned, fully-managed device scenarios over BYOD/work
profile scenarios, per the product mandate. Work-profile support is an
architectural consideration (the `ownership_type` and `management_mode`
enums reserve space for it) but not a Phase 1–4 deliverable.
