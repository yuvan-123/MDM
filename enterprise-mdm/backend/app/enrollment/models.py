"""Enrollment ORM models: EnrollmentToken, DeviceEnrollment.

Two distinct concepts:
- EnrollmentToken: the credential an admin generates and hands to a device
  (or its user). Follows the exact raw-token-never-stored pattern as
  OrganizationInvitation.token_hash (Phase 3) — only the SHA-256 hash is
  persisted, the raw value is returned once, at creation, and never again.
- DeviceEnrollment: one record per enrollment attempt, 1:1 with the Device
  it produces (the Device row is created immediately at registration —
  see app/enrollment/service.py — there is no "pending device with no
  Device row yet"). This is the authoritative state-machine and audit
  trail for how that specific device came to be enrolled; Device.enrollment_id
  points back to it, and Device.enrollment_status is a denormalized
  convenience copy of DeviceEnrollment.status kept in sync by the service
  layer, so device list/filter queries never need to join this table.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.devices.enums import DevicePlatform, DeviceType


class EnrollmentTokenStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    EXHAUSTED = "EXHAUSTED"


class DeviceEnrollmentStatus(str, enum.Enum):
    """See app/devices/enums.py::EnrollmentStatus for why APPROVED exists
    but is never set by any Phase 5 code path."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    ENROLLED = "ENROLLED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class EnrollmentToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "enrollment_tokens"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="SHA-256 hex of the raw token. Raw token returned once at "
        "creation, never persisted or logged.",
    )
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[EnrollmentTokenStatus] = mapped_column(
        Enum(EnrollmentTokenStatus, name="enrollment_token_status"),
        nullable=False,
        default=EnrollmentTokenStatus.ACTIVE,
        index=True,
    )
    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Optional pre-association (STEP 2: "Optionally associate the enrollment with").
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    allowed_platform: Mapped[DevicePlatform | None] = mapped_column(
        Enum(DevicePlatform, name="device_platform"), nullable=True
    )
    allowed_device_type: Mapped[DeviceType | None] = mapped_column(
        Enum(DeviceType, name="device_type"), nullable=True
    )

    # --- Phase 11: real provider-side provisioning ---
    provision_via_provider: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If true, token creation also called the real (or mock) "
        "platform provider's enroll_device to mint a genuine provider-side "
        "enrollment credential for allowed_platform — see "
        "app/enrollment/service.py::create_token. Only meaningful when "
        "allowed_platform is set.",
    )
    provider_enrollment_reference: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        index=True,
        comment="The provider's own non-secret resource name for the minted "
        "enrollment credential (e.g. Android's enrollmentTokens/{id} "
        "resource name) — safe to persist and display, unlike the secret "
        "value itself, which is returned once at creation and never stored. "
        "Doubles as the resolution key for a first-time provider check-in: "
        "a real Android device's own resource later reports this exact "
        "string back as its `enrollmentTokenName`, which is how "
        "app/enrollment/service.py::resolve_provider_checkin recovers which "
        "organization a never-before-seen device belongs to. Never set for "
        "a token that did not successfully provision_via_provider.",
    )
    provider_enrollment_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="The provider-reported expiration of the minted credential, "
        "which may differ from this token's own expires_at.",
    )

    organization = relationship("Organization", lazy="noload")

    def __repr__(self) -> str:
        return f"<EnrollmentToken id={self.id} org={self.organization_id} status={self.status}>"


class DeviceEnrollment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_enrollments"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enrollment_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enrollment_tokens.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[DeviceEnrollmentStatus] = mapped_column(
        Enum(DeviceEnrollmentStatus, name="device_enrollment_status"),
        nullable=False,
        default=DeviceEnrollmentStatus.PENDING,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Copied from the token's expires_at at registration time, "
        "so a later token change never retroactively affects this request.",
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization = relationship("Organization", lazy="noload")
    device = relationship("Device", foreign_keys=[device_id], lazy="noload")

    def __repr__(self) -> str:
        return f"<DeviceEnrollment id={self.id} device={self.device_id} status={self.status}>"
