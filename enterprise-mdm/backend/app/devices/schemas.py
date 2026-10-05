"""Pydantic schemas for the device inventory API.

Notes:
- `organization_id` never appears on any request schema — it is always
  derived server-side from the resolved OrganizationContext (see
  app/devices/router.py). See STEP 7 of the Phase 4 spec / docs/security/device-security.md.
- `imei` / `wifi_mac` are write-only on CreateDeviceRequest: accepted, hashed,
  and reduced to a last-4 fragment before ever touching the database. They
  are not echoed back and have no corresponding "full value" field on any
  response schema.
- `status` and `enrollment_status` are never client-settable through the
  generic create/update schemas — status transitions go through the
  dedicated lifecycle endpoints (assign/unassign/block/unblock/retire) so
  every transition is independently validated and audited (STEP 9).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.devices.enums import (
    ConnectivityState,
    DevicePlatform,
    DeviceStatus,
    DeviceType,
    EnrollmentStatus,
    OwnershipType,
)

if TYPE_CHECKING:
    from app.devices.models import Device

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CreateDeviceRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    model_identifier: str | None = Field(default=None, max_length=100)
    platform: DevicePlatform
    operating_system: str | None = Field(default=None, max_length=100)
    os_version: str | None = Field(default=None, max_length=50)
    device_type: DeviceType
    ownership_type: OwnershipType
    serial_number: str | None = Field(default=None, max_length=200)
    device_identifier: str | None = Field(default=None, max_length=200)
    # Write-only: hashed immediately, raw value never persisted or returned.
    imei: str | None = Field(default=None, min_length=1, max_length=64)
    wifi_mac: str | None = Field(default=None, min_length=1, max_length=64)


class UpdateDeviceRequest(BaseModel):
    """Descriptive-field update, plus the non-privileged lifecycle slice.

    `status`, if provided, may only move the device among ACTIVE, INACTIVE,
    and LOST (validated in DeviceService.update_device) — BLOCKED and
    RETIRED are reachable only through their own permissioned endpoints
    (device.block / device.retire). `enrollment_status`, `organization_id`,
    and `assigned_user_id` are not settable here at all.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    manufacturer: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    model_identifier: str | None = Field(default=None, max_length=100)
    operating_system: str | None = Field(default=None, max_length=100)
    os_version: str | None = Field(default=None, max_length=50)
    security_patch_level: str | None = Field(default=None, max_length=50)
    ownership_type: OwnershipType | None = None
    status: DeviceStatus | None = None
    serial_number: str | None = Field(default=None, max_length=200)
    device_identifier: str | None = Field(default=None, max_length=200)


class AssignDeviceRequest(BaseModel):
    user_id: uuid.UUID


class HeartbeatRequest(BaseModel):
    """Device-reported check-in payload (STEP 4). Every field is optional —
    a bare heartbeat with no health fields still updates `last_seen_at`."""

    client_version: str | None = Field(default=None, max_length=50)
    os_version: str | None = Field(default=None, max_length=50)
    security_patch_level: str | None = Field(default=None, max_length=50)
    is_encrypted: bool | None = None
    has_screen_lock: bool | None = None
    is_rooted: bool | None = None


class HeartbeatResponse(BaseModel):
    device_id: uuid.UUID
    last_seen_at: datetime
    connectivity_state: ConnectivityState


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID

    device_identifier: str | None
    serial_number: str | None
    display_name: str

    manufacturer: str | None
    model: str | None
    model_identifier: str | None

    platform: DevicePlatform
    operating_system: str | None
    os_version: str | None
    security_patch_level: str | None

    device_type: DeviceType
    ownership_type: OwnershipType

    status: DeviceStatus
    enrollment_status: EnrollmentStatus

    assigned_user_id: uuid.UUID | None
    assigned_user_email: str | None = None
    assigned_user_full_name: str | None = None

    # Populated only for callers holding device.view_sensitive; None otherwise.
    imei_last4: str | None = None
    wifi_mac_last4: str | None = None

    last_seen_at: datetime | None
    last_check_in_at: datetime | None
    enrolled_at: datetime | None
    last_enrollment_attempt_at: datetime | None
    connectivity_state: ConnectivityState

    client_version: str | None = None
    is_encrypted: bool | None = None
    has_screen_lock: bool | None = None
    is_rooted: bool | None = None

    # Phase 12: populated by the router from ProviderDeviceLinkRepository,
    # not by from_device() below (which has no DB access) — see
    # app/devices/router.py's _enrich_with_provider_state. Distinguishes
    # "enrolled" (this codebase has a device identity/check-in channel,
    # Phase 5) from "managed" (a real provider identity exists and remote
    # commands can actually be dispatched, Phase 9-11) — the same
    # distinction Device's own module docstring has drawn since Phase 5,
    # now finally surfaced through the API instead of only in code comments.
    management_state: Literal["MANAGED", "UNMANAGED"] = "UNMANAGED"
    provider_name: str | None = None
    provider_device_id: str | None = None

    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_device(cls, device: Device, *, include_sensitive: bool) -> DeviceResponse:
        """Single source of truth for ORM -> response construction, used by
        both app/devices/router.py and app/enrollment/router.py (device
        registration returns a DeviceResponse too) — see STEP 18's
        sensitive-field gating, applied identically in both places."""
        from app.devices.connectivity import compute_connectivity_state

        return cls(
            id=device.id,
            organization_id=device.organization_id,
            device_identifier=device.device_identifier,
            serial_number=device.serial_number,
            display_name=device.display_name,
            manufacturer=device.manufacturer,
            model=device.model,
            model_identifier=device.model_identifier,
            platform=device.platform,
            operating_system=device.operating_system,
            os_version=device.os_version,
            security_patch_level=device.security_patch_level,
            device_type=device.device_type,
            ownership_type=device.ownership_type,
            status=device.status,
            enrollment_status=device.enrollment_status,
            assigned_user_id=device.assigned_user_id,
            assigned_user_email=device.assigned_user.email if device.assigned_user else None,
            assigned_user_full_name=(
                device.assigned_user.full_name if device.assigned_user else None
            ),
            imei_last4=device.imei_last4 if include_sensitive else None,
            wifi_mac_last4=device.wifi_mac_last4 if include_sensitive else None,
            last_seen_at=device.last_seen_at,
            last_check_in_at=device.last_check_in_at,
            enrolled_at=device.enrolled_at,
            last_enrollment_attempt_at=device.last_enrollment_attempt_at,
            connectivity_state=compute_connectivity_state(device.last_seen_at),
            client_version=device.client_version,
            is_encrypted=device.is_encrypted,
            has_screen_lock=device.has_screen_lock,
            is_rooted=device.is_rooted,
            created_at=device.created_at,
            updated_at=device.updated_at,
        )


class DeviceListResponse(BaseModel):
    items: list[DeviceResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
