"""Platform-provider-specific error types.

Both are AppError subclasses so they flow through the existing global
exception handlers (app/core/errors.py) unchanged — no new handler needed.
"""

from __future__ import annotations

from fastapi import status

from app.core.errors import AppError


class PlatformProviderUnavailableError(AppError):
    """Raised when a real provider (Android/Apple) is selected but its
    required credentials/configuration are absent. Always raised at
    provider construction time — never mid-dispatch — so a caller never
    sees a half-completed command because credentials turned out to be
    missing partway through (see app/platform/registry.py).

    In this development environment `platform_provider_mode` is never set
    to "real" by default and no real credentials are configured, so this
    is expected, honest behavior if someone flips that setting here — not
    a bug to silence."""

    def __init__(self, provider_name: str, detail: str) -> None:
        super().__init__(
            code="PLATFORM_PROVIDER_UNAVAILABLE",
            message=f"The {provider_name} platform provider is not available: {detail}",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )


class PlatformCommandRejectedError(AppError):
    """Raised when a reachable, configured provider's own API rejects a
    dispatched command (malformed request, unsupported combination on that
    specific device, platform-side quota/error). Distinct from
    PlatformProviderUnavailable: the provider IS configured and reachable —
    this one command failed."""

    def __init__(self, provider_name: str, detail: str) -> None:
        super().__init__(
            code="PLATFORM_COMMAND_REJECTED",
            message=f"The {provider_name} platform rejected this command: {detail}",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
