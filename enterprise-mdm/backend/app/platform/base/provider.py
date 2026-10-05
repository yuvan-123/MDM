"""PlatformManagementProvider — the platform abstraction boundary.

See docs/architecture/android-enterprise.md and
docs/architecture/device-commands.md. Reserved since Phase 1 to fix the
shape of the codebase; Phase 6 is the first phase that calls through this
interface for real (via app/commands/service.py), against a
`MockManagementProvider` by default (app/platform/mock/provider.py) — the
concrete `AndroidManagementProvider` / `AppleManagementProvider` subclasses
implement real request/response mapping against current official API
documentation, but are never selected unless `platform_provider_mode="real"`
is explicitly configured, and raise `PlatformProviderUnavailable`
(app/platform/base/errors.py) immediately at construction if required
credentials are absent — which they always are in this development
environment. No concrete subclass may claim a real device action occurred
without a provider that was actually reachable and actually accepted it.

Do not add Android-specific or Apple-specific parameter types here — keep
this interface platform-neutral so every concrete provider can implement it
identically. `ProviderDeviceRef`/`ProviderCommandRequest`/
`ProviderCommandResult` below are deliberately plain dataclasses, not the
SQLAlchemy `Device` model, so this module never imports the ORM.
"""

from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.devices.enums import DevicePlatform
from app.platform.base.capabilities import PlatformCapability


class ProviderCommandStatus(str, enum.Enum):
    """What a provider is reporting about ONE dispatched command,
    immediately after its dispatch call returns — intentionally a small
    set. A provider method reports only what it can truthfully know
    synchronously:

    - SENT: the platform accepted the command for asynchronous delivery
      (Android Management API's issueCommand, an APNs push) but its actual
      outcome is not yet known here — this codebase has no completion
      callback listener (Android Pub/Sub subscriber, Apple MDM check-in
      endpoint); see docs/architecture/device-commands.md for why that is
      deliberately deferred rather than faked.
    - COMPLETED: the provider can assert the action is done, either because
      it is a synchronous read (e.g. a state query) or because it is a
      deterministic simulation (MockManagementProvider).
    - FAILED: the provider's own call failed or was rejected.

    `app.commands.models.DeviceCommandStatus` is the superset the command
    record actually stores — it also includes PENDING (before dispatch is
    even attempted) and CANCELLED/EXPIRED (server-side lifecycle states no
    provider call alone could ever produce).
    """

    SENT = "SENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProviderDeviceRef:
    """The minimum a provider needs to address one device."""

    device_id: uuid.UUID
    platform: DevicePlatform
    provider_device_id: str | None = None


@dataclass(frozen=True)
class ProviderCommandRequest:
    device: ProviderDeviceRef
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderCommandResult:
    status: ProviderCommandStatus
    provider_command_id: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class ProviderEnrollmentToken:
    """The result of `PlatformManagementProvider.enroll_device` (Phase 11)
    — a provider-minted credential a real device uses to provision itself
    directly against the platform (Android's own enrollment flow, entirely
    bypassing this backend until the device later checks in).

    `value` is the secret a device needs to enroll — treated exactly like
    this codebase's own enrollment-token raw value: returned to the caller
    once, never persisted. `reference` is the provider's own non-secret
    resource name for the credential itself (e.g. Android's
    `enterprises/{id}/enrollmentTokens/{tokenId}`) — safe to persist, and
    is exactly the value a real enrolled device's own resource later
    reports back as `enrollmentTokenName` (see
    app/enrollment/service.py::resolve_provider_checkin), which is how a
    first-time check-in resolves back to the organization that minted it."""

    value: str
    reference: str | None = None
    qr_code: str | None = None
    expires_at: datetime | None = None


class PlatformManagementProvider(ABC):
    """Abstract boundary between core MDM domain logic and a specific
    device-management platform (Mock, Android Enterprise, Apple MDM)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short machine-readable identifier — "mock" | "android" | "apple"
        — persisted on DeviceCommand.provider_name at dispatch time, even
        if configuration later changes."""

    @property
    @abstractmethod
    def supported_capabilities(self) -> frozenset[PlatformCapability]:
        """Which PlatformCapability values this provider can attempt at
        all. Checked by CommandService BEFORE any dispatch call — an
        unsupported capability never reaches lock_device/wipe_device/etc.
        This answers "can this provider even try," not "will this specific
        attempt succeed" (that's what ProviderCommandResult.status is for).
        """

    @abstractmethod
    async def enroll_device(self, *args: Any, **kwargs: Any) -> Any:
        """Phase 11: mint a provider-side enrollment credential
        (`ProviderEnrollmentToken`) for a real device to provision itself
        against, or raise `PlatformProviderUnavailableError` documenting
        why this provider can't (see AppleManagementProvider). Deliberately
        left `*args/**kwargs` rather than a fixed signature — Android's
        real mechanism (a policy-bound enrollment token) and Apple's (which
        this codebase does not implement) have no shared parameter shape."""

    @abstractmethod
    async def get_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Used for the REFRESH_INFO capability."""

    @abstractmethod
    async def get_devices(self, *args: Any, **kwargs: Any) -> Any: ...

    @abstractmethod
    async def apply_policy(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Used for the POLICY_SYNC capability."""

    @abstractmethod
    async def remove_policy(self, *args: Any, **kwargs: Any) -> Any: ...

    @abstractmethod
    async def install_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 14: used for the APPLICATION_INSTALL capability. Same
        request/result shape as lock_device/wipe_device/etc — app
        deployment is dispatched through the identical
        DeviceCommand/CommandService path (see app/applications/service.py),
        not a parallel mechanism. `request.payload` carries the
        platform-specific app identifier (`package_name` for Android,
        `bundle_identifier` for Apple — see Application's own model docstring
        for why those stay isolated fields on the catalog row itself)."""

    @abstractmethod
    async def remove_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 14: used for the APPLICATION_REMOVE capability — the
        uninstall counterpart to install_application above."""

    @abstractmethod
    async def lock_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Used for the LOCK capability."""

    @abstractmethod
    async def restart_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Used for the REBOOT capability."""

    @abstractmethod
    async def wipe_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Used for the WIPE capability."""

    @abstractmethod
    async def unenroll_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        """Phase 12: remove a device from real platform management (the
        DeviceCommand-driven `UNENROLL` capability — distinct from
        `enroll_device` above, which mints a *new* enrollment credential
        during Phase 11's provisioning flow, not this: severing an
        *existing* managed device). Only implemented where the platform's
        real API genuinely supports it (Android's `devices.delete`) — see
        each concrete provider for what it actually does or why it
        doesn't."""

    @abstractmethod
    async def retire_device(self, *args: Any, **kwargs: Any) -> Any: ...

    @abstractmethod
    async def get_compliance_state(self, *args: Any, **kwargs: Any) -> Any: ...
