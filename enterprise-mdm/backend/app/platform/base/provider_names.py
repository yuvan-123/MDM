"""Shared provider-name vocabulary (Phase 11).

`app/provider_events/link_service.py` (Phase 10) and `app/enrollment/service.py`
(Phase 11) both need to validate a caller-supplied `provider_name` string
against the same small set — pulled out here rather than duplicated, since
it's the one place in the codebase that has to agree with itself on what a
"provider" is.
"""

from __future__ import annotations

from app.devices.enums import DevicePlatform

VALID_PROVIDER_NAMES = frozenset({"android", "apple", "mock"})


def platform_to_provider_name(platform: DevicePlatform) -> str | None:
    """The provider a given device platform resolves through when
    `platform_provider_mode="real"` — see `PlatformProviderRegistry.get_provider`,
    which this mirrors. Returns None for a platform with no real provider
    (`OTHER`) — there is nothing to auto-link a device to in that case."""
    if platform == DevicePlatform.ANDROID:
        return "android"
    if platform in (DevicePlatform.IOS, DevicePlatform.IPADOS):
        return "apple"
    return None
