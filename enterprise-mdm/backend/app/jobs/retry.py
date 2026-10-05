"""Transient-vs-permanent error classification for background-job retries.

A standalone classifier keyed by `AppError.code`, rather than a new field on
`PlatformProviderUnavailableError`/`PlatformCommandRejectedError`
(app/platform/base/errors.py) — neither is actually raised by any Phase 7
job-processing path yet, so touching Phase 6's verified error classes for a
capability nothing exercises today was judged unjustified risk. This can
absorb future transient codes without touching them.
"""

from __future__ import annotations

from app.core.errors import AppError

# Codes worth retrying — the failure is about a dependency being
# momentarily unreachable/unconfigured, not about the job's target being
# wrong or gone.
_TRANSIENT_ERROR_CODES = frozenset({"PLATFORM_PROVIDER_UNAVAILABLE"})


def is_transient(exc: Exception) -> bool:
    """A recognized AppError not in the transient set (e.g. DEVICE_NOT_FOUND
    — the device was deleted, retrying can never help) is permanent. Any
    other exception (a DB hiccup, an unanticipated bug) is treated as
    transient: the safer default is to retry rather than silently drop a
    job that failed for a reason nothing here anticipated."""
    if isinstance(exc, AppError):
        return exc.code in _TRANSIENT_ERROR_CODES
    return True
