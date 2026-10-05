"""Server-side connectivity state computation (STEP 4).

Pure function, no I/O — a device's ONLINE/STALE/OFFLINE state is always
derived from `last_seen_at` at read time, never trusted from client input
and never a stored column that could drift out of sync.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import get_settings
from app.devices.enums import ConnectivityState


def compute_connectivity_state(last_seen_at: datetime | None) -> ConnectivityState:
    if last_seen_at is None:
        return ConnectivityState.NEVER_CHECKED_IN

    # SQLite (test harness) drops tzinfo on round-trip even for
    # DateTime(timezone=True) columns; Postgres does not. Normalize to
    # UTC before subtracting, matching the established fix in
    # app/auth/models.py::Session.is_active.
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)

    settings = get_settings()
    now = datetime.now(tz=UTC)
    age = now - last_seen_at

    if age.total_seconds() <= settings.device_online_threshold_minutes * 60:
        return ConnectivityState.ONLINE
    if age.total_seconds() <= settings.device_stale_threshold_hours * 3600:
        return ConnectivityState.STALE
    return ConnectivityState.OFFLINE
