"""Device ORM model — inventory (Phase 4) + enrollment/health (Phase 5).

IMPORTANT — three distinct concepts:

1. INVENTORY   "This device exists in the organization." <- Phase 4
2. ENROLLMENT  "This device has been registered with the MDM."  <- Phase 5
               real state machine, see app/enrollment/.
3. MANAGEMENT  "The MDM can actually control this device."      <- future
               Android/Apple integration phases only, still not started.

A Device row existing, or `status=ACTIVE`, or `enrollment_status=ENROLLED`,
NEVER implies the platform can issue a real command to the physical
device — enrollment here means "the backend has a device identity and an
authenticated check-in channel," not "MDM control." See
docs/architecture/device-management.md and
docs/architecture/enrollment-and-policy.md.

Tenancy: every device belongs to exactly one organization
(`organization_id`, NOT NULL, FK, indexed) — the same mandatory-tenant
pattern established in Phase 3 for OrganizationMembership. There is no
`get_by_id`-style bare lookup anywhere for this model; see
app/repositories/device_repository.py.

Sensitive identifiers (IMEI, Wi-Fi MAC, device_secret_hash) are never
stored in the clear — see app/devices/identity.py and
docs/security/device-security.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.devices.enums import (
    DevicePlatform,
    DeviceStatus,
    DeviceType,
    EnrollmentStatus,
    OwnershipType,
)


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A device inventory record scoped to exactly one organization."""

    __tablename__ = "devices"
    __table_args__ = (
        # NULLs are distinct under both Postgres and SQLite unique
        # constraints, so multiple devices per org may omit either field
        # while any *provided* value must still be unique within the org.
        UniqueConstraint(
            "organization_id", "device_identifier", name="uq_device_org_identifier"
        ),
        UniqueConstraint("organization_id", "serial_number", name="uq_device_org_serial"),
        UniqueConstraint("organization_id", "imei_hash", name="uq_device_org_imei_hash"),
        Index("ix_devices_org_status", "organization_id", "status"),
        Index("ix_devices_org_platform", "organization_id", "platform"),
        Index("ix_devices_org_enrollment_status", "organization_id", "enrollment_status"),
        Index("ix_devices_org_assigned_user", "organization_id", "assigned_user_id"),
        Index("ix_devices_org_created_at", "organization_id", "created_at"),
        Index("ix_devices_org_ownership", "organization_id", "ownership_type"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # --- Identity ---
    device_identifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)

    # --- Hardware ---
    manufacturer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_identifier: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # --- Platform ---
    platform: Mapped[DevicePlatform] = mapped_column(
        Enum(DevicePlatform, name="device_platform"), nullable=False, index=True
    )
    operating_system: Mapped[str | None] = mapped_column(String(100), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    security_patch_level: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # --- Classification ---
    device_type: Mapped[DeviceType] = mapped_column(
        Enum(DeviceType, name="device_type"), nullable=False
    )
    ownership_type: Mapped[OwnershipType] = mapped_column(
        Enum(OwnershipType, name="ownership_type"), nullable=False
    )

    # --- Lifecycle ---
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, name="device_status"),
        nullable=False,
        default=DeviceStatus.ACTIVE,
        index=True,
    )
    enrollment_status: Mapped[EnrollmentStatus] = mapped_column(
        Enum(EnrollmentStatus, name="enrollment_status"),
        nullable=False,
        default=EnrollmentStatus.NOT_ENROLLED,
        index=True,
    )

    # --- Assignment ---
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- Sensitive identifiers (hash + last4 only; see app/devices/identity.py) ---
    imei_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    imei_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    wifi_mac_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wifi_mac_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # --- Check-in / enrollment timestamps ---
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_check_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrolled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_enrollment_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Phase 5: enrollment linkage + device-reported health (via heartbeat) ---
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "device_enrollments.id",
            ondelete="SET NULL",
            # devices <-> device_enrollments is a genuine circular FK
            # (device_enrollments.device_id -> devices.id, NOT NULL, is
            # the other half). use_alter tells SQLAlchemy this side is
            # created via a separate ALTER TABLE, matching what the
            # migration does explicitly — without it, Base.metadata
            # create_all/drop_all (used by the test harness) can't
            # topologically sort the two tables and emits a warning.
            use_alter=True,
            name="fk_devices_enrollment_id",
        ),
        nullable=True,
        index=True,
        comment="The DeviceEnrollment record that created/last touched this "
        "device, if any. NULL for devices created manually via Phase 4's "
        "admin-entry flow rather than self-registration.",
    )
    device_secret_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="SHA-256 hash of the device's own bearer credential, issued "
        "once at enrollment completion (see app/enrollment/service.py). "
        "NEVER expose this field through any API response.",
    )
    client_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_encrypted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    has_screen_lock: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_rooted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    organization: Mapped[Organization] = relationship("Organization", lazy="noload")  # noqa: F821
    assigned_user: Mapped[User | None] = relationship(  # noqa: F821
        "User", foreign_keys=[assigned_user_id], lazy="noload"
    )

    def __repr__(self) -> str:
        return (
            f"<Device id={self.id} org={self.organization_id} "
            f"platform={self.platform} status={self.status}>"
        )


# Avoid circular imports at module level.
from app.organizations.models import Organization  # noqa: E402, F401
from app.users.models import User  # noqa: E402, F401
