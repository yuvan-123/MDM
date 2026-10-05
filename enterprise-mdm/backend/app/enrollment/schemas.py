"""Pydantic schemas for the enrollment API.

`CreateEnrollmentTokenResponse` is the only schema that ever carries the
raw token value — returned exactly once, at creation. Every other
enrollment-token schema exposes only non-secret metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.devices.enums import DevicePlatform, DeviceType, OwnershipType
from app.devices.schemas import DeviceResponse
from app.enrollment.models import DeviceEnrollmentStatus, EnrollmentTokenStatus

# ---------------------------------------------------------------------------
# Enrollment tokens
# ---------------------------------------------------------------------------


class CreateEnrollmentTokenRequest(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    expires_in_hours: int = Field(default=24, ge=1, le=24 * 90)
    require_approval: bool = Field(default=True)
    max_uses: int = Field(default=1, ge=1, le=10_000)
    target_user_id: uuid.UUID | None = None
    allowed_platform: DevicePlatform | None = None
    allowed_device_type: DeviceType | None = None
    provision_via_provider: bool = Field(
        default=False,
        description="Also mint a real (or mock, depending on deployment mode) "
        "provider-side enrollment credential — requires allowed_platform to be "
        "set. See docs/architecture/phase-11.md.",
    )


class EnrollmentTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    label: str | None
    status: EnrollmentTokenStatus
    require_approval: bool
    max_uses: int
    use_count: int
    expires_at: datetime
    revoked_at: datetime | None
    target_user_id: uuid.UUID | None
    allowed_platform: DevicePlatform | None
    allowed_device_type: DeviceType | None
    provision_via_provider: bool
    provider_enrollment_reference: str | None
    provider_enrollment_expires_at: datetime | None
    created_at: datetime


class CreateEnrollmentTokenResponse(EnrollmentTokenResponse):
    token: str = Field(description="Raw enrollment token — shown once, never retrievable again.")
    provider_enrollment_value: str | None = Field(
        default=None,
        description="The real provider-minted enrollment credential a device "
        "provisions itself against directly (e.g. Android's QR/enrollment "
        "token value) — shown once, never retrievable again, never logged. "
        "Only present when provision_via_provider was requested and succeeded.",
    )
    provider_enrollment_qr_code: str | None = Field(
        default=None, description="Provider-supplied QR code payload, if any."
    )


# ---------------------------------------------------------------------------
# Device registration (unauthenticated, token-gated)
# ---------------------------------------------------------------------------


class RegisterDeviceRequest(BaseModel):
    token: str = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=200)
    platform: DevicePlatform
    device_type: DeviceType
    ownership_type: OwnershipType
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    os_version: str | None = Field(default=None, max_length=50)
    serial_number: str | None = Field(default=None, max_length=200)
    device_identifier: str | None = Field(default=None, max_length=200)
    client_version: str | None = Field(default=None, max_length=50)
    provider_device_id: str | None = Field(
        default=None,
        max_length=255,
        description="This device's own provider-native identifier (Android's "
        "device resource id, or Apple's UDID) — when supplied, a "
        "ProviderDeviceLink is created automatically alongside the device. "
        "Required if the platform is ANDROID or IOS/IPADOS and the caller "
        "wants outbound MDM commands to ever be dispatchable; omit for a "
        "device with no real provider identity yet.",
    )
    apple_push_token: str | None = Field(
        default=None,
        max_length=255,
        description="Apple only — the device's current APNs push token, if "
        "already known at registration time. See ProviderDeviceLink.apple_push_token.",
    )


class RegisterDeviceResponse(BaseModel):
    device: DeviceResponse
    device_id: uuid.UUID
    device_secret: str = Field(
        description="Raw device credential — shown once, never retrievable again. "
        "Required (as X-Device-Id/X-Device-Secret headers) for heartbeat calls."
    )
    enrollment_id: uuid.UUID
    enrollment_status: DeviceEnrollmentStatus
    provider_link_created: bool = Field(
        default=False,
        description="True when provider_device_id was supplied and a "
        "ProviderDeviceLink now exists for this device.",
    )


class DeviceEnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    enrollment_token_id: uuid.UUID | None
    status: DeviceEnrollmentStatus
    expires_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejected_by: uuid.UUID | None
    rejected_at: datetime | None
    rejection_reason: str | None
    revoked_by: uuid.UUID | None
    revoked_at: datetime | None
    created_at: datetime
    provider_name: str | None = Field(
        default=None, description="Set when this device has a ProviderDeviceLink."
    )
    provider_device_id: str | None = Field(
        default=None, description="Set when this device has a ProviderDeviceLink."
    )


class RejectEnrollmentRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
