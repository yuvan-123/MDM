"""Enrollment API router.

Two routers, mounted separately in app/main.py:

  enrollment_router (prefix "/enrollment"):
    POST   /enrollment/tokens              [org] enrollment_token.create
    GET    /enrollment/tokens              [org] enrollment_token.read
    DELETE /enrollment/tokens/{id}         [org] enrollment_token.revoke (revokes, not deleted)
    POST   /enrollment/register            unauthenticated, token-gated

  device_enrollment_router (prefix "/devices", same prefix as
  app/devices/router.py — FastAPI allows multiple routers sharing a
  prefix):
    GET  /devices/{device_id}/enrollment          [org] device.read
    POST /devices/{device_id}/enrollment/approve  [org] device.enroll
    POST /devices/{device_id}/enrollment/reject   [org] device.enroll
    POST /devices/{device_id}/enrollment/revoke   [org] device.enroll

Phase 5 vocabulary note: the spec names admin actions "suspend"/"restore"
for the device lifecycle — those already exist as
POST /devices/{id}/block and POST /devices/{id}/unblock (Phase 4) and are
intentionally NOT duplicated here. "Revoke" in this module means revoking
a device's *enrollment* specifically (clearing its heartbeat credential),
which is a distinct action from the Phase 4 lifecycle. See
docs/architecture/enrollment-and-policy.md for the full vocabulary
mapping.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import check_enrollment_rate_limit
from app.core.errors import AppError
from app.database.session import get_db_session
from app.devices.dependencies import require_device_permission
from app.devices.schemas import DeviceResponse
from app.enrollment.dependencies import get_enrollment_service
from app.enrollment.schemas import (
    CreateEnrollmentTokenRequest,
    CreateEnrollmentTokenResponse,
    DeviceEnrollmentResponse,
    EnrollmentTokenResponse,
    RegisterDeviceRequest,
    RegisterDeviceResponse,
    RejectEnrollmentRequest,
)
from app.enrollment.service import EnrollmentService
from app.organizations.dependencies import OrganizationContext
from app.platform.base.provider_names import platform_to_provider_name
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user
from app.repositories.device_enrollment_repository import DeviceEnrollmentRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

enrollment_router = APIRouter(prefix="/enrollment", tags=["enrollment"])
device_enrollment_router = APIRouter(prefix="/devices", tags=["enrollment"])

MAX_PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


@enrollment_router.post(
    "/tokens", response_model=CreateEnrollmentTokenResponse, status_code=status.HTTP_201_CREATED
)
async def create_enrollment_token(
    body: CreateEnrollmentTokenRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.ENROLLMENT_TOKEN_CREATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: EnrollmentService = Depends(get_enrollment_service),
) -> CreateEnrollmentTokenResponse:
    token, raw_token, provider_enrollment_value, provider_enrollment_qr_code = (
        await service.create_token(
            org_context.organization_id,
            actor_user_id=current_user.user_id,
            label=body.label,
            expires_in_hours=body.expires_in_hours,
            require_approval=body.require_approval,
            max_uses=body.max_uses,
            target_user_id=body.target_user_id,
            allowed_platform=body.allowed_platform,
            allowed_device_type=body.allowed_device_type,
            provision_via_provider=body.provision_via_provider,
        )
    )
    return CreateEnrollmentTokenResponse(
        **EnrollmentTokenResponse.model_validate(token).model_dump(),
        token=raw_token,
        provider_enrollment_value=provider_enrollment_value,
        provider_enrollment_qr_code=provider_enrollment_qr_code,
    )


@enrollment_router.get("/tokens", response_model=list[EnrollmentTokenResponse])
async def list_enrollment_tokens(
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.ENROLLMENT_TOKEN_READ))
    ],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: EnrollmentService = Depends(get_enrollment_service),
) -> list[EnrollmentTokenResponse]:
    tokens, _ = await service.list_tokens(org_context.organization_id, offset=offset, limit=limit)
    return [EnrollmentTokenResponse.model_validate(t) for t in tokens]


@enrollment_router.delete("/tokens/{token_id}", response_model=EnrollmentTokenResponse)
async def revoke_enrollment_token(
    token_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.ENROLLMENT_TOKEN_REVOKE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: EnrollmentService = Depends(get_enrollment_service),
) -> EnrollmentTokenResponse:
    """Revokes the token (status -> REVOKED). Not a hard SQL delete —
    DeviceEnrollment history references tokens and must remain intact."""
    token = await service.revoke_token(
        org_context.organization_id, token_id, actor_user_id=current_user.user_id
    )
    return EnrollmentTokenResponse.model_validate(token)


# ---------------------------------------------------------------------------
# Device registration (unauthenticated — the token is the proof of authorization)
# ---------------------------------------------------------------------------


@enrollment_router.post(
    "/register", response_model=RegisterDeviceResponse, status_code=status.HTTP_201_CREATED
)
async def register_device(
    body: RegisterDeviceRequest,
    request: Request,
    service: EnrollmentService = Depends(get_enrollment_service),
) -> RegisterDeviceResponse:
    # Phase 15: this endpoint is unauthenticated (the enrollment token is
    # the sole authorization) — rate limit per IP the same way /auth/login
    # already is, so it isn't a free token-brute-force target. Fails open
    # if Redis is unavailable, same as every other limiter in this
    # codebase — never blocks a real device's registration on that basis.
    forwarded = request.headers.get("X-Forwarded-For")
    client_ip = (
        forwarded.split(",")[0].strip()
        if forwarded
        else (request.client.host if request.client else "unknown")
    )
    await check_enrollment_rate_limit(client_ip)

    device, enrollment, device_secret, provider_link_created = await service.register_device(
        raw_token=body.token,
        display_name=body.display_name,
        platform=body.platform,
        device_type=body.device_type,
        ownership_type=body.ownership_type,
        manufacturer=body.manufacturer,
        model=body.model,
        os_version=body.os_version,
        serial_number=body.serial_number,
        device_identifier=body.device_identifier,
        client_version=body.client_version,
        provider_device_id=body.provider_device_id,
        apple_push_token=body.apple_push_token,
    )
    # The registering caller has no OrganizationContext (unauthenticated,
    # token-gated) so it can never hold device.view_sensitive.
    device_response = DeviceResponse.from_device(device, include_sensitive=False)
    if provider_link_created:
        # Phase 12: keep this response's management_state consistent with
        # provider_link_created — Path A (Phase 11) can create a real
        # ProviderDeviceLink in this very call, and app/devices/router.py's
        # own responses already reflect that; this one must too.
        device_response = device_response.model_copy(
            update={
                "management_state": "MANAGED",
                "provider_name": platform_to_provider_name(body.platform),
                "provider_device_id": body.provider_device_id,
            }
        )
    return RegisterDeviceResponse(
        device=device_response,
        device_id=device.id,
        device_secret=device_secret,
        enrollment_id=enrollment.id,
        enrollment_status=enrollment.status,
        provider_link_created=provider_link_created,
    )


# ---------------------------------------------------------------------------
# Per-device enrollment detail + approve/reject/revoke
# ---------------------------------------------------------------------------


async def _get_enrollment_or_404(
    org_context: OrganizationContext, device_id: uuid.UUID, db: AsyncSession
) -> DeviceEnrollmentResponse:
    repo = DeviceEnrollmentRepository(db)
    enrollment = await repo.get_by_device_id(org_context.organization_id, device_id)
    if enrollment is None:
        raise AppError(
            code="DEVICE_ENROLLMENT_NOT_FOUND",
            message="This device has no enrollment record.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    response = DeviceEnrollmentResponse.model_validate(enrollment)
    link = await ProviderDeviceLinkRepository(db).get_any_link_for_device(
        org_context.organization_id, device_id
    )
    if link is not None:
        response = response.model_copy(
            update={
                "provider_name": link.provider_name,
                "provider_device_id": link.provider_device_id,
            }
        )
    return response


@device_enrollment_router.get(
    "/{device_id}/enrollment", response_model=DeviceEnrollmentResponse
)
async def get_device_enrollment(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_READ))
    ],
    db: AsyncSession = Depends(get_db_session),
) -> DeviceEnrollmentResponse:
    return await _get_enrollment_or_404(org_context, device_id, db)


@device_enrollment_router.post(
    "/{device_id}/enrollment/approve", response_model=DeviceEnrollmentResponse
)
async def approve_device_enrollment(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_ENROLL))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: EnrollmentService = Depends(get_enrollment_service),
) -> DeviceEnrollmentResponse:
    enrollment = await service.approve_enrollment(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return DeviceEnrollmentResponse.model_validate(enrollment)


@device_enrollment_router.post(
    "/{device_id}/enrollment/reject", response_model=DeviceEnrollmentResponse
)
async def reject_device_enrollment(
    device_id: uuid.UUID,
    body: RejectEnrollmentRequest,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_ENROLL))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: EnrollmentService = Depends(get_enrollment_service),
) -> DeviceEnrollmentResponse:
    enrollment = await service.reject_enrollment(
        org_context.organization_id,
        device_id,
        actor_user_id=current_user.user_id,
        reason=body.reason,
    )
    return DeviceEnrollmentResponse.model_validate(enrollment)


@device_enrollment_router.post(
    "/{device_id}/enrollment/revoke", response_model=DeviceEnrollmentResponse
)
async def revoke_device_enrollment(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_device_permission(p.DEVICE_ENROLL))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: EnrollmentService = Depends(get_enrollment_service),
) -> DeviceEnrollmentResponse:
    enrollment = await service.revoke_enrollment(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return DeviceEnrollmentResponse.model_validate(enrollment)
