"""FastAPI dependencies for the enrollment module.

Two distinct trust models live here, matching the two channels the module
serves:
- Admin endpoints (create/list/revoke tokens, approve/reject/revoke a
  device's enrollment): standard JWT-authenticated admin, tenant-scoped via
  `require_current_organization_permission` (X-Organization-ID header),
  identical to every other Phase 4/5 module.
- `get_enrolled_device`: the device-to-backend channel (heartbeat). A real
  device has no platform-user JWT, so it authenticates with its own
  bearer credential instead — `X-Device-Id` + `X-Device-Secret` headers,
  the raw secret returned exactly once at enrollment completion, verified
  here against the stored SHA-256 hash. This is a genuine, minimally
  scoped credential check, not a stub: the backend independently derives
  the device's own organization_id from the database row it finds, never
  from anything the caller claims.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.database.session import get_db_session
from app.devices.enums import DeviceStatus, EnrollmentStatus
from app.devices.models import Device
from app.enrollment.identity import hash_token
from app.enrollment.service import EnrollmentService
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_enrollment_repository import DeviceEnrollmentRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.enrollment_token_repository import EnrollmentTokenRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

DEVICE_ID_HEADER = "X-Device-Id"
DEVICE_SECRET_HEADER = "X-Device-Secret"


def get_enrollment_service(db: AsyncSession = Depends(get_db_session)) -> EnrollmentService:
    return EnrollmentService(
        token_repo=EnrollmentTokenRepository(db),
        enrollment_repo=DeviceEnrollmentRepository(db),
        device_repo=DeviceRepository(db),
        link_repo=ProviderDeviceLinkRepository(db),
        audit_repo=AuditRepository(db),
        registry=PlatformProviderRegistry(),
    )


async def get_enrolled_device(
    x_device_id: str = Header(alias=DEVICE_ID_HEADER),
    x_device_secret: str = Header(alias=DEVICE_SECRET_HEADER),
    db: AsyncSession = Depends(get_db_session),
) -> Device:
    """Authenticate a device for the heartbeat channel.

    Deliberately returns the SAME generic error for every failure mode
    (unknown id, wrong secret, revoked, blocked, retired, not yet
    enrolled) so a caller probing device IDs can't distinguish "wrong
    secret" from "device doesn't exist" from "device was revoked."
    """
    try:
        device_id = uuid.UUID(x_device_id)
    except ValueError as exc:
        raise invalid_device_credential_error() from exc

    device_repo = DeviceRepository(db)
    device = await device_repo.get_by_id_unscoped(device_id)

    if (
        device is None
        or device.device_secret_hash is None
        or device.device_secret_hash != hash_token(x_device_secret)
        or device.enrollment_status != EnrollmentStatus.ENROLLED
        or device.status in (DeviceStatus.RETIRED, DeviceStatus.BLOCKED)
    ):
        raise invalid_device_credential_error()

    return device


def invalid_device_credential_error() -> AppError:
    return AppError(
        code="DEVICE_CREDENTIAL_INVALID",
        message="Device credential is invalid, expired, or revoked.",
        status_code=status.HTTP_401_UNAUTHORIZED,
    )
