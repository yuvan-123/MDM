"""MockManagementProvider — deterministic, simulated PlatformManagementProvider.

Per docs/architecture/android-enterprise.md §5 (mock adapter policy):
- Selected ONLY via the explicit `platform_provider_mode="mock"` setting
  (the default) — never a silent fallback (see app/platform/registry.py).
- Every response string says "Simulated" — never "Device locked," always
  "Simulated: device lock command accepted."
- No API response, log line, or UI copy generated through this provider
  may claim a real device action occurred.

Every command resolves deterministically to COMPLETED, unless the caller
opts into failure via `payload["simulate_failure"] = True` (a test-only
hook — real device commands never carry this key). This makes Mock the
only provider where COMPLETED is available for every capability
synchronously, which is why it's the default for tests and local dev: it
lets the full command lifecycle be exercised without any real platform
being reachable. See ProviderCommandStatus's docstring
(app/platform/base/provider.py) for why Android/Apple providers cannot
make the same synchronous COMPLETED claim for push-delivered commands.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.platform.base.capabilities import PlatformCapability
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


class MockManagementProvider(PlatformManagementProvider):
    @property
    def name(self) -> str:
        return "mock"

    @property
    def supported_capabilities(self) -> frozenset[PlatformCapability]:
        return _ALL_CAPABILITIES

    def _simulate(self, request: ProviderCommandRequest, action: str) -> ProviderCommandResult:
        """`simulate_failure` and `simulate_sent` are test-only payload
        keys — a real command payload never carries them. Without
        `simulate_sent`, Mock resolves every command straight to COMPLETED
        (see the module docstring for why that's honest for Mock
        specifically); tests that need to exercise the SENT state (e.g.
        cancellation, which is only legal from SENT) opt in explicitly
        rather than that state being unreachable in the default test
        suite."""
        if request.payload.get("simulate_failure"):
            return ProviderCommandResult(
                status=ProviderCommandStatus.FAILED,
                error_code="MOCK_SIMULATED_FAILURE",
                error_message=f"Simulated: {action} was rejected (test-only simulate_failure flag)",
            )
        if request.payload.get("simulate_sent"):
            return ProviderCommandResult(
                status=ProviderCommandStatus.SENT,
                provider_command_id=f"mock-{uuid.uuid4()}",
                result={"message": f"Simulated: {action} command accepted, delivery pending."},
            )
        return ProviderCommandResult(
            status=ProviderCommandStatus.COMPLETED,
            provider_command_id=f"mock-{uuid.uuid4()}",
            result={"message": f"Simulated: {action} command accepted and completed."},
        )

    async def lock_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "device lock")

    async def restart_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "device reboot")

    async def wipe_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "device wipe")

    async def apply_policy(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "policy sync")

    async def unenroll_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "device unenrollment")

    async def install_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "application install")

    async def remove_application(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        return self._simulate(request, "application removal")

    async def get_device(self, request: ProviderCommandRequest) -> ProviderCommandResult:
        result = self._simulate(request, "device info refresh")
        if result.status != ProviderCommandStatus.COMPLETED:
            return result
        return ProviderCommandResult(
            status=result.status,
            provider_command_id=result.provider_command_id,
            result={"os_version": "Simulated OS 1.0", "client_version": "mock-agent-1.0"},
        )

    async def enroll_device(
        self, *, policy_id: str, duration_seconds: int = 3600
    ) -> ProviderEnrollmentToken:
        """Phase 11: deterministic simulated enrollment credential — lets
        the provider-provisioned enrollment-token path (EnrollmentService)
        be exercised end to end in tests without real Android/Apple
        credentials, the same role Mock already plays for every other
        provider verb. `policy_id` is folded into the fake `reference` so a
        test can assert traceability back to the value EnrollmentService
        generated."""
        reference = f"mock/enterprises/mock-enterprise/enrollmentTokens/{policy_id}"
        return ProviderEnrollmentToken(
            value=f"mock-enrollment-token-{uuid.uuid4()}",
            reference=reference,
            qr_code=None,
            expires_at=None,
        )

    # --- Reserved, out of scope for Phase 6 — see app/platform/base/provider.py ---

    async def get_devices(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("MockManagementProvider.get_devices is out of Phase 6 scope.")

    async def remove_policy(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("MockManagementProvider.remove_policy is out of Phase 6 scope.")

    async def retire_device(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError("MockManagementProvider.retire_device is out of Phase 6 scope.")

    async def get_compliance_state(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError(
            "MockManagementProvider.get_compliance_state is out of Phase 6 scope."
        )
