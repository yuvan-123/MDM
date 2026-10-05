"""AppleManagementProvider — real Apple MDM protocol integration shape.

Phase 9: APNs HTTP/2 transport and provider-token caching now live in
app/platform/apple/client.py::ApnsApiClient — this class is domain-verb
mapping only, never constructing an httpx request itself.

Built against Apple's currently-documented MDM command schema (verified
via apple/device-management, Apple's own canonical machine-readable schema
repository, in August 2026 — see docs/apple/mdm-capabilities.md for the
full capability matrix and citations). NEVER live-verified against a real
device: this development environment has no Apple Push Notification
service (APNs) certificate/key, no MDM push topic, and no Apple Business
Manager enrollment identity. This class raises
PlatformProviderUnavailableError immediately at construction if required
settings are absent — which is always true here.

## Protocol shape (context for why this provider only goes halfway)
Apple MDM is a two-channel protocol, fundamentally different from
Android's single authenticated REST API:
1. The server sends a silent APNs push (just a wake-up signal — no command
   payload) to the device's push token.
2. The device independently opens an HTTPS connection back to the
   server's own /mdm command-queue endpoint and long-polls for the next
   queued command, then POSTs a plist-encoded Status response
   (Acknowledged / NotNow / Error / CommandFormatError) once it has
   attempted it.
Building channel 2 — an HTTPS endpoint the device itself calls, plus the
plist command-queue/response handling — is genuinely missing
infrastructure in this codebase, not merely unconfigured credentials; see
docs/architecture/device-commands.md "Known Gaps." Per this phase's
explicit instruction ("implement the provider interface and clearly
document the missing infrastructure rather than faking successful
commands"), this provider implements channel 1 only (constructing a
correctly-shaped MDM command and sending the APNs wake-up push) and raises
PlatformProviderUnavailableError for the rest.

## Command mapping (see docs/apple/mdm-capabilities.md for citations)
- LOCK          -> DeviceLock command (RequestType=DeviceLock; optional
                   Message, PhoneNumber, PIN fields).
- WIPE          -> EraseDevice command (RequestType=EraseDevice; optional
                   PIN, DisallowProximitySetup, PreserveDataPlan fields).
- REFRESH_INFO  -> DeviceInformation query command.
- REBOOT        -> NOT a supported capability for this provider. Apple's
                   published MDM command schema has no iOS/iPadOS
                   REBOOT-equivalent (macOS has a separate RestartDevice
                   command, out of scope — this codebase only enrolls
                   ANDROID/IOS/IPADOS, see app/devices/enums.py::DevicePlatform).
                   This is a deliberate, documented gap from actually
                   checking the schema, not an oversight — per this
                   phase's instruction not to assume every platform
                   supports every command.
- POLICY_SYNC   -> NOT a supported capability for this provider in this
                   implementation. Apple's InstallProfile command is the
                   real mechanism, but its full profile-payload schema was
                   not part of this research pass — declaring it
                   "supported" without having actually verified the
                   payload shape would violate "do not invent undocumented
                   API behavior." Left unsupported and documented rather
                   than guessed.

## A second, independent reason every real dispatch attempt fails today
Even with APNs credentials configured, no device in this system has a
stored APNs push token or MDM "PushMagic" value — both are only issued
during genuine Apple MDM enrollment, which Phase 5's synthetic,
backend-only enrollment flow never performs. `ProviderDeviceRef` has no
field for either today (out of scope for Phase 6 — would require its own
migration and real Apple enrollment flow), so every dispatch method below
fails fast with a distinct, clear error for that reason too.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import Settings
from app.platform.apple.client import ApnsApiClient
from app.platform.base.capabilities import PlatformCapability
from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError
from app.platform.base.provider import (
    PlatformManagementProvider,
    ProviderCommandRequest,
    ProviderCommandResult,
    ProviderCommandStatus,
)

# LOCK, WIPE, REFRESH_INFO only — see module docstring for why REBOOT and
# POLICY_SYNC are deliberately absent rather than assumed.
_SUPPORTED_CAPABILITIES = frozenset(
    {
        PlatformCapability.LOCK,
        PlatformCapability.WIPE,
        PlatformCapability.REFRESH_INFO,
    }
)


class AppleManagementProvider(PlatformManagementProvider):
    def __init__(self, settings: Settings) -> None:
        if not (
            settings.apple_apns_key_path
            and settings.apple_apns_key_id
            and settings.apple_apns_team_id
            and settings.apple_apns_topic
        ):
            raise PlatformProviderUnavailableError(
                "apple",
                "APPLE_APNS_KEY_PATH, APPLE_APNS_KEY_ID, APPLE_APNS_TEAM_ID, and "
                "APPLE_APNS_TOPIC must all be configured to use the real Apple provider.",
            )
        self._client = ApnsApiClient(
            settings.apple_apns_key_path,
            settings.apple_apns_key_id,
            settings.apple_apns_team_id,
            settings.apple_apns_topic,
        )

    @property
    def name(self) -> str:
        return "apple"

    @property
    def supported_capabilities(self) -> frozenset[PlatformCapability]:
        return _SUPPORTED_CAPABILITIES

    def _require_push_identity(self, request: ProviderCommandRequest) -> str:
        # ProviderDeviceRef.provider_device_id is the one generic dispatch
        # identifier this interface exposes — for an Apple command,
        # CommandService._dispatch (Phase 10) resolves it specifically from
        # ProviderDeviceLink.apple_push_token, not the UDID column, before
        # ever constructing this request. See that method and
        # ProviderDeviceLink's own docstring for why those are genuinely
        # different values for Apple.
        push_token = request.device.provider_device_id
        if not push_token:
            raise PlatformCommandRejectedError(
                "apple",
                "This device has no APNs push token on record — it was never "
                "enrolled through the real Apple MDM protocol in this deployment.",
            )
        return push_token

    async def _dispatch_mdm_command(
        self, request: ProviderCommandRequest, request_type: str, command_fields: dict[str, Any]
    ) -> ProviderCommandResult:
        push_token = self._require_push_identity(request)
        command_uuid = str(uuid.uuid4())
        # The full command plist ({"CommandUUID": ..., "Command": {"RequestType":
        # request_type, **command_fields}}) is reconstructed from this
        # DeviceCommand row itself when the device actually checks in over
        # channel 2 (app/platform/apple/checkin.py) — this call only sends
        # the wake-up push (channel 1) telling the device to go ask.
        await self._client.send_wakeup_push(push_token)
        return ProviderCommandResult(
            status=ProviderCommandStatus.SENT,
            provider_command_id=command_uuid,
            result={"request_type": request_type, "command_uuid": command_uuid},
        )

    async def lock_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        message = request.payload.get("message")
        fields: dict[str, Any] = {"Message": message} if message else {}
        return await self._dispatch_mdm_command(request, "DeviceLock", fields)

    async def wipe_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return await self._dispatch_mdm_command(request, "EraseDevice", {})

    async def get_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return await self._dispatch_mdm_command(request, "DeviceInformation", {})

    async def restart_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        raise PlatformCommandRejectedError(
            "apple",
            "REBOOT is not a supported capability on this provider — see "
            "docs/apple/mdm-capabilities.md.",
        )

    async def apply_policy(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        raise PlatformCommandRejectedError(
            "apple",
            "POLICY_SYNC is not a supported capability on this provider — see "
            "docs/apple/mdm-capabilities.md.",
        )

    async def unenroll_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 12: not a supported capability — Apple's real removal
        mechanism (the device processing a `RemoveProfile` command against
        its own MDM profile, or a user/admin removing it from Apple
        Business Manager) has no single push-and-confirm shape analogous to
        Android's `devices.delete`, and was not part of this codebase's
        researched capability set (see docs/apple/mdm-capabilities.md).
        `UNENROLL` is absent from `_SUPPORTED_CAPABILITIES` above, so
        CommandService's capability gate rejects this before it would ever
        be called — this body exists only so the class remains
        instantiable and fails the same documented way REBOOT/POLICY_SYNC
        do if ever reached directly."""
        raise PlatformCommandRejectedError(
            "apple",
            "UNENROLL is not a supported capability on this provider — see "
            "docs/apple/mdm-capabilities.md.",
        )

    async def install_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 14: not a supported capability. Apple's real mechanism
        (the InstallApplication MDM command) requires an app manifest/IPA
        URL and, for App Store apps, a VPP (Volume Purchase Program)
        content token — neither of which this codebase has any
        integration for, and whose payload schema was not part of this
        research pass (same "do not invent undocumented API behavior" line
        already drawn for POLICY_SYNC above). `APPLICATION_INSTALL` is
        absent from `_SUPPORTED_CAPABILITIES`, so CommandService's
        capability gate rejects this before it would ever be called."""
        raise PlatformCommandRejectedError(
            "apple",
            "APPLICATION_INSTALL is not a supported capability on this provider — see "
            "docs/architecture/phase-14.md.",
        )

    async def remove_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 14: not supported, for the same reason as install_application
        above — Apple's RemoveApplication command has the same missing
        VPP-token integration gap."""
        raise PlatformCommandRejectedError(
            "apple",
            "APPLICATION_REMOVE is not a supported capability on this provider — see "
            "docs/architecture/phase-14.md.",
        )

    async def enroll_device(self, *args: Any, **kwargs: Any) -> Any:
        """Phase 11: real Apple zero-touch enrollment (Automated Device
        Enrollment via Apple Business Manager) requires a categorically
        different integration than anything this codebase has built —
        server-to-server ABM API access with its own OAuth client
        credentials, entirely separate from the APNs push certificate this
        provider already uses for command dispatch. No such credentials
        exist anywhere in this environment, and building that client is
        out of Phase 11 scope (see docs/architecture/phase-11.md "Apple
        enrollment"). This raises the same honest, documented
        "unavailable" error every other real-provider gap in this codebase
        raises, rather than a bare NotImplementedError or a faked success —
        callers (app/enrollment/service.py) let it propagate as a clear
        503, never silently skip provider provisioning."""
        raise PlatformProviderUnavailableError(
            "apple",
            "Real Apple enrollment (Automated Device Enrollment via Apple Business "
            "Manager) is not implemented — it requires separate server-to-server "
            "ABM credentials, categorically different from the APNs push "
            "certificate already configured for command dispatch. Use this "
            "codebase's own generic enrollment token plus the device's UDID "
            "(provider_device_id at registration, or the Phase 10 admin API) to "
            "link an Apple device instead.",
        )

    # --- Reserved, out of scope for Phase 6 — see app/platform/base/provider.py ---

    async def get_devices(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("AppleManagementProvider.get_devices is out of Phase 6 scope.")

    async def remove_policy(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("AppleManagementProvider.remove_policy is out of Phase 6 scope.")

    async def retire_device(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("AppleManagementProvider.retire_device is out of Phase 6 scope.")

    async def get_compliance_state(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "AppleManagementProvider.get_compliance_state is out of Phase 6 scope."
        )
