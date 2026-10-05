"""Device inventory enums.

All device classification fields are closed enums, not free strings — an
operator cannot insert an arbitrary platform/status value through the API.
Extending these lists is a code change (and, for Postgres, a migration
`ADD VALUE`), matching the pattern established for OrganizationStatus /
MembershipStatus in Phase 3.
"""

from __future__ import annotations

import enum


class DevicePlatform(str, enum.Enum):
    ANDROID = "ANDROID"
    IOS = "IOS"
    IPADOS = "IPADOS"
    OTHER = "OTHER"


class DeviceType(str, enum.Enum):
    PHONE = "PHONE"
    TABLET = "TABLET"
    OTHER = "OTHER"


class OwnershipType(str, enum.Enum):
    CORPORATE = "CORPORATE"
    BYOD = "BYOD"
    COPE = "COPE"
    COBO = "COBO"


class DeviceStatus(str, enum.Enum):
    """Inventory/application lifecycle state — NOT the real-world state of
    the physical device (see app/devices/models.py module docstring)."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    LOST = "LOST"
    BLOCKED = "BLOCKED"
    RETIRED = "RETIRED"


class EnrollmentStatus(str, enum.Enum):
    """Phase 4 shipped this column with no code path ever populating
    anything but NOT_ENROLLED. Phase 5 implements the real state machine —
    see app/enrollment/service.py and docs/architecture/enrollment-and-policy.md.

    NOT_ENROLLED -> PENDING -> ENROLLED -> REVOKED
                       |
                       +-> REJECTED
                       +-> EXPIRED

    APPROVED is reserved for a future two-step device-side confirmation
    flow (an admin approving a pending request does not yet wait on any
    further action from a real device agent, since none exists) — no
    Phase 5 code path sets it. Documented rather than removed so a future
    phase doesn't have to re-derive why it's here.
    """

    NOT_ENROLLED = "NOT_ENROLLED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    ENROLLED = "ENROLLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class ConnectivityState(str, enum.Enum):
    """Computed at read time from `last_seen_at` — never stored as a
    column (STEP 4: "do not rely only on a manually supplied status
    field... use timestamps and server-side logic"). See
    app/devices/connectivity.py."""

    ONLINE = "ONLINE"
    STALE = "STALE"
    OFFLINE = "OFFLINE"
    NEVER_CHECKED_IN = "NEVER_CHECKED_IN"
