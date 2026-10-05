"""Provider registry — selects a PlatformManagementProvider for a given
device platform, per the current `platform_provider_mode` setting.

Per docs/architecture/android-enterprise.md §5 (mandatory mock adapter
policy): the mock provider is selected ONLY via this explicit setting,
never as a silent fallback for missing real credentials — if
`platform_provider_mode="real"`, this registry always attempts to
construct a real provider, which itself raises
`PlatformProviderUnavailableError` (never silently substitutes Mock) if its
required credentials are absent. In this development environment, that is
always the outcome of setting `platform_provider_mode="real"` — there are
no real credentials configured anywhere.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.devices.enums import DevicePlatform
from app.platform.base.errors import PlatformProviderUnavailableError
from app.platform.base.provider import PlatformManagementProvider
from app.platform.mock.provider import MockManagementProvider


class PlatformProviderRegistry:
    """Resolves the right provider for one device's platform. Injected into
    CommandService rather than a single fixed provider instance, since the
    correct choice depends on each command's own device (see
    app/commands/service.py)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def platform_provider_mode(self) -> str:
        """Exposes the mode this registry was actually configured with —
        CommandService must read the mode from here, never from the
        ambient global `get_settings()`, so that a registry constructed
        with an explicit Settings override (as every provider-related test
        does) is the single source of truth for both provider selection
        AND the `provider_name` recorded on a command. Reading the global
        singleton instead was a real bug caught by
        test_command_platform_provider_unavailable.py: a command created
        through a "real"-mode registry was still recorded with
        `provider_name="mock"`, because the ambient default is "mock" and
        nothing ever pointed CommandService at this registry's own
        setting."""
        return self._settings.platform_provider_mode

    def get_provider(self, platform: DevicePlatform) -> PlatformManagementProvider:
        if self._settings.platform_provider_mode == "mock":
            return MockManagementProvider()

        # platform_provider_mode == "real" — each concrete provider raises
        # PlatformProviderUnavailableError itself, at construction, if its
        # required credentials are absent (always true in this
        # environment). This registry never silently substitutes Mock for
        # that failure.
        if platform == DevicePlatform.ANDROID:
            from app.platform.android.provider import AndroidManagementProvider

            return AndroidManagementProvider(self._settings)
        if platform in (DevicePlatform.IOS, DevicePlatform.IPADOS):
            from app.platform.apple.provider import AppleManagementProvider

            return AppleManagementProvider(self._settings)

        raise PlatformProviderUnavailableError(
            provider_name=platform.value.lower(),
            detail="No real platform provider exists for this device platform.",
        )
