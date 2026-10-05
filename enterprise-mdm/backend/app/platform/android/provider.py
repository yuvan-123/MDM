"""AndroidManagementProvider — real Android Management API integration.

Phase 9: HTTP transport, OAuth2 token caching, and error classification
now live in app/platform/android/client.py::AndroidApiClient — this class
is domain-verb mapping only (LOCK -> issueCommand(type=LOCK), etc.), never
constructing an httpx request itself.

Built against Google's currently-documented API surface (verified via
developers.google.com in August 2026 — see
docs/android/enterprise-capabilities.md for the full capability matrix and
citations). NEVER live-verified against a real Android Enterprise account:
this development environment has no Docker, no real Google Cloud
service-account credentials, and no enterprise binding. This class raises
PlatformProviderUnavailableError immediately at construction if required
settings are absent — which is always true here — per
app/platform/base/provider.py's rule that no concrete provider may claim a
real device action occurred without genuinely being reachable and
configured.

## Auth model
Android Management API authenticates via a Google Cloud service-account
key, exchanged for a short-lived OAuth2 access token using the standard
RFC 7523 JWT-Bearer flow: a self-signed RS256 JWT (iss=client_email,
scope, aud=token endpoint, iat/exp) is POSTed to Google's token endpoint,
which returns a bearer access token used on every subsequent REST call.
Required scope: `https://www.googleapis.com/auth/androidmanagement`.

## Command mapping (see docs/android/enterprise-capabilities.md for the
citation backing each row)
- LOCK          -> enterprises.devices.issueCommand(type=LOCK)
- REBOOT        -> enterprises.devices.issueCommand(type=REBOOT)
- WIPE          -> enterprises.devices.issueCommand(type=WIPE, wipeParams).
                   Factory-resets a company-owned device / deletes the
                   work profile on a personally-owned one, per Google's
                   documentation. Deliberately NOT
                   enterprises.devices.delete (a blunter "delete the
                   device record, best-effort wipe if reachable"
                   operation) — WIPE is the correct mapping for an
                   explicit, in-place device action that keeps the
                   device's inventory record intact.
- REFRESH_INFO  -> enterprises.devices.issueCommand(type=REQUEST_DEVICE_INFO).
                   Chosen over the simpler enterprises.devices.get (a
                   synchronous read of Google's own, possibly-stale cached
                   resource) because "refresh" should mean "ask the device
                   right now," not "read what we already have." This keeps
                   every Android command type consistently asynchronous
                   (SENT-only, never COMPLETED) — see
                   ProviderCommandStatus's docstring in
                   app/platform/base/provider.py.
- POLICY_SYNC   -> enterprises.devices.patch, re-asserting policyName.
                   Android Management API has no distinct "sync now" push
                   command; the device applies its assigned policy
                   automatically on its next check-in once policyName is
                   set. This is best-effort reconciliation, not a
                   guaranteed immediate sync — documented, not glossed
                   over.
- UNENROLL      -> enterprises.devices.delete (Phase 12) — the same
                   "blunter" operation WIPE deliberately avoids (see
                   above), used here for exactly the case it's correct
                   for: removing the device from management entirely, not
                   an in-place action. A direct synchronous REST delete,
                   not an issueCommand Operation, so this is the one
                   Android command type that can honestly report
                   COMPLETED rather than SENT.

issueCommand returns a long-running `Operation`, not a synchronous result:
this provider reports SENT immediately with the Operation's `name` as
`provider_command_id`. Polling `operations.get` for eventual completion
would need a scheduled job this codebase does not have — deferred, see
docs/architecture/device-commands.md, not faked here.

## A second, independent reason every real dispatch attempt fails today
Even with credentials configured, no device in this system has ever been
enrolled through the *real* Android Management API — Phase 5's enrollment
flow is a synthetic, backend-only identity (a random secret + SHA-256
hash), not genuine Android Enterprise enrollment. So
`ProviderDeviceRef.provider_device_id` (the Android Management API device
resource name) is never populated, and every dispatch method below fails
fast with a distinct, clear error for that reason — separate from, and in
addition to, the missing-credentials failure at construction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.config import Settings
from app.platform.android.client import AndroidApiClient
from app.platform.base.capabilities import PlatformCapability
from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError
from app.platform.base.provider import (
    PlatformManagementProvider,
    ProviderCommandRequest,
    ProviderCommandResult,
    ProviderCommandStatus,
    ProviderEnrollmentToken,
)

_ALL_CAPABILITIES = frozenset(
    {
        PlatformCapability.LOCK,
        PlatformCapability.REBOOT,
        PlatformCapability.WIPE,
        PlatformCapability.REFRESH_INFO,
        PlatformCapability.POLICY_SYNC,
        PlatformCapability.UNENROLL,
        PlatformCapability.APPLICATION_INSTALL,
        PlatformCapability.APPLICATION_REMOVE,
    }
)


class AndroidManagementProvider(PlatformManagementProvider):
    def __init__(self, settings: Settings) -> None:
        if not settings.android_service_account_json or not settings.android_enterprise_id:
            raise PlatformProviderUnavailableError(
                "android",
                "ANDROID_SERVICE_ACCOUNT_JSON and ANDROID_ENTERPRISE_ID must both be "
                "configured to use the real Android provider.",
            )
        self._client = AndroidApiClient(
            settings.android_service_account_json, settings.android_enterprise_id
        )

    @property
    def name(self) -> str:
        return "android"

    @property
    def supported_capabilities(self) -> frozenset[PlatformCapability]:
        return _ALL_CAPABILITIES

    def _require_provider_device_id(self, request: ProviderCommandRequest) -> str:
        provider_device_id = request.device.provider_device_id
        if not provider_device_id:
            raise PlatformCommandRejectedError(
                "android",
                "This device has no Android Management API device identifier on "
                "record — it was never enrolled through the real Android "
                "Management API in this deployment.",
            )
        return provider_device_id

    async def _issue_command(
        self, request: ProviderCommandRequest, command_type: str, params: dict[str, Any]
    ) -> ProviderCommandResult:
        provider_device_id = self._require_provider_device_id(request)
        operation = await self._client.issue_command(provider_device_id, command_type, params)
        return ProviderCommandResult(
            status=ProviderCommandStatus.SENT,
            provider_command_id=operation.get("name"),
            result={"operation_name": operation.get("name"), "command_type": command_type},
        )

    async def lock_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return await self._issue_command(request, "LOCK", {})

    async def restart_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return await self._issue_command(request, "REBOOT", {})

    async def wipe_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return await self._issue_command(request, "WIPE", {"wipeParams": {}})

    async def get_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return await self._issue_command(request, "REQUEST_DEVICE_INFO", {})

    async def apply_policy(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        provider_device_id = self._require_provider_device_id(request)
        policy_name = f"enterprises/{self._client.enterprise_id}/policies/default"
        await self._client.patch_device(
            provider_device_id, "policyName", {"policyName": policy_name}
        )
        return ProviderCommandResult(
            status=ProviderCommandStatus.SENT,
            result={"policy_name": policy_name},
        )

    async def _set_application_install_type(
        self, request: ProviderCommandRequest, package_name: str, *, install_type: str | None
    ) -> ProviderCommandResult:
        """Phase 14: Android Management API has no per-device "install this
        app now" push command (unlike LOCK/REBOOT/WIPE's issueCommand) —
        app deployment is declarative via the enterprise policy's
        `applications` array; a device picks up additions/removals on its
        next policy sync, the same best-effort, not-guaranteed-immediate
        model already documented for POLICY_SYNC above. This reuses that
        exact `enterprises/{id}/policies/default` resource
        apply_policy already assumes every device in this codebase is on —
        so, like POLICY_SYNC, this affects every device on that shared
        policy, not only the one this command was issued against.
        `install_type=None` removes the package_name entry entirely
        (uninstall); otherwise it is (re)added with that installType.
        Still requires this device to have a real Android Management API
        identifier on record, as every other real dispatch method does —
        an unlinked device can never trigger a real provider side-effect
        through this codebase."""
        self._require_provider_device_id(request)
        policy_name = f"enterprises/{self._client.enterprise_id}/policies/default"
        policy = await self._client.get_policy(policy_name)
        applications = [
            app
            for app in policy.get("applications", [])
            if app.get("packageName") != package_name
        ]
        if install_type is not None:
            applications.append({"packageName": package_name, "installType": install_type})
        await self._client.patch_policy(policy_name, "applications", {"applications": applications})
        return ProviderCommandResult(
            status=ProviderCommandStatus.SENT,
            result={
                "policy_name": policy_name,
                "package_name": package_name,
                "action": "installed" if install_type is not None else "removed",
            },
        )

    async def install_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        package_name = request.payload.get("package_name")
        if not package_name:
            raise PlatformCommandRejectedError(
                "android", "install_application requires a package_name in the request payload."
            )
        # AVAILABLE (not FORCE_INSTALLED) — makes the app available to the
        # device without unilaterally forcing an install the moment the
        # policy syncs, matching this codebase's "not guaranteed immediate,
        # best-effort" framing rather than overclaiming enforcement.
        return await self._set_application_install_type(
            request, package_name, install_type="AVAILABLE"
        )

    async def remove_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        package_name = request.payload.get("package_name")
        if not package_name:
            raise PlatformCommandRejectedError(
                "android", "remove_application requires a package_name in the request payload."
            )
        return await self._set_application_install_type(request, package_name, install_type=None)

    async def unenroll_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 12: `enterprises.devices.delete` — real removal from
        Android management, not a simulated/local-only state change. Unlike
        every other real Android dispatch method, this genuinely completes
        synchronously (a direct REST delete, not a long-running
        `issueCommand` Operation), so it can honestly report COMPLETED
        rather than SENT — see this method's own client-side docstring."""
        provider_device_id = self._require_provider_device_id(request)
        await self._client.delete_device(provider_device_id)
        return ProviderCommandResult(
            status=ProviderCommandStatus.COMPLETED,
            result={"unenrolled": True},
        )

    async def enroll_device(
        self, *, policy_id: str, duration_seconds: int = 3600
    ) -> ProviderEnrollmentToken:
        """Phase 11: `enterprises.enrollmentTokens.create` — mints a real,
        policy-bound enrollment credential. `policy_id` is the caller's
        (EnrollmentService's) own uniquely-generated choice; this method
        owns turning it into Android's fully-qualified policy resource name
        so a device checking in under it can later be traced back to
        exactly one organization — see app/enrollment/service.py and
        docs/architecture/phase-11.md."""
        policy_name = f"enterprises/{self._client.enterprise_id}/policies/{policy_id}"
        data = await self._client.create_enrollment_token(
            policy_name=policy_name, duration_seconds=duration_seconds
        )
        expires_at: datetime | None = None
        raw_expiry = data.get("expirationTimestamp")
        if isinstance(raw_expiry, str) and raw_expiry:
            try:
                expires_at = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00"))
            except ValueError:
                expires_at = None
        return ProviderEnrollmentToken(
            value=data["value"],
            reference=data.get("name"),
            qr_code=data.get("qrCode"),
            expires_at=expires_at,
        )

    # --- Reserved, out of scope for Phase 6 — see app/platform/base/provider.py ---

    async def get_devices(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("AndroidManagementProvider.get_devices is out of Phase 6 scope.")

    async def remove_policy(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AndroidManagementProvider.remove_policy is out of Phase 6 scope."
        )

    async def retire_device(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AndroidManagementProvider.retire_device is out of Phase 6 scope."
        )

    async def get_compliance_state(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AndroidManagementProvider.get_compliance_state is out of Phase 6 scope."
        )
