"""Compliance API router.

  GET  /compliance/devices/{device_id}   [org] compliance.read
  GET  /compliance/summary                [org] compliance.read
  GET  /compliance/non-compliant           [org] compliance.read
  POST /compliance/evaluate/{device_id}    [org] compliance.evaluate

  Policy violations (Phase 13) — mutating routes gated by
  compliance.remediation.manage, reads by compliance.read:
  GET  /compliance/violations                  (paginated, ?device_id=&policy_id=&status=)
  GET  /compliance/violations/{violation_id}
  POST /compliance/violations/{violation_id}/retry
  POST /compliance/violations/{violation_id}/resolve
  POST /compliance/violations/{violation_id}/disable-remediation
  POST /compliance/violations/{violation_id}/enable-remediation
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.compliance.dependencies import get_compliance_service, require_compliance_permission
from app.compliance.models import DeviceCompliance, PolicyViolation, ViolationStatus
from app.compliance.schemas import (
    ComplianceSummaryResponse,
    DeviceComplianceListResponse,
    DeviceComplianceResponse,
    PolicyViolationListResponse,
    PolicyViolationResponse,
    RuleResultResponse,
)
from app.compliance.service import ComplianceService
from app.organizations.dependencies import OrganizationContext
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/compliance", tags=["compliance"])

MAX_PAGE_SIZE = 100


def _violation_to_response(violation: PolicyViolation) -> PolicyViolationResponse:
    return PolicyViolationResponse.model_validate(violation)


def _to_response(compliance: DeviceCompliance) -> DeviceComplianceResponse:
    details = compliance.details or []
    return DeviceComplianceResponse(
        device_id=compliance.device_id,
        organization_id=compliance.organization_id,
        status=compliance.status,
        policy_id=compliance.policy_id,
        policy_version_id=compliance.policy_version_id,
        evaluated_at=compliance.evaluated_at,
        details=[RuleResultResponse.model_validate(d) for d in details],
    )


@router.get("/devices/{device_id}", response_model=DeviceComplianceResponse)
async def get_device_compliance(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_READ))
    ],
    service: ComplianceService = Depends(get_compliance_service),
) -> DeviceComplianceResponse:
    compliance = await service.get_device_compliance(org_context.organization_id, device_id)
    return _to_response(compliance)


@router.get("/summary", response_model=ComplianceSummaryResponse)
async def get_compliance_summary(
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_READ))
    ],
    service: ComplianceService = Depends(get_compliance_service),
) -> ComplianceSummaryResponse:
    summary = await service.get_summary(org_context.organization_id)
    return ComplianceSummaryResponse(**summary)


@router.get("/non-compliant", response_model=DeviceComplianceListResponse)
async def list_non_compliant_devices(
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_READ))
    ],
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: ComplianceService = Depends(get_compliance_service),
) -> DeviceComplianceListResponse:
    items, total = await service.list_non_compliant(
        org_context.organization_id, offset=offset, limit=limit
    )
    return DeviceComplianceListResponse(
        items=[_to_response(c) for c in items], total=total
    )


@router.post("/evaluate/{device_id}", response_model=DeviceComplianceResponse)
async def evaluate_device_compliance(
    device_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_EVALUATE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ComplianceService = Depends(get_compliance_service),
) -> DeviceComplianceResponse:
    compliance = await service.evaluate_device(
        org_context.organization_id, device_id, actor_user_id=current_user.user_id
    )
    return _to_response(compliance)


# ---------------------------------------------------------------------------
# Policy violations (Phase 13)
# ---------------------------------------------------------------------------


@router.get("/violations", response_model=PolicyViolationListResponse)
async def list_policy_violations(
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_READ))
    ],
    device_id: uuid.UUID | None = Query(default=None),
    policy_id: uuid.UUID | None = Query(default=None),
    status_filter: ViolationStatus | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE_SIZE),
    service: ComplianceService = Depends(get_compliance_service),
) -> PolicyViolationListResponse:
    items, total = await service.list_violations(
        org_context.organization_id,
        device_id=device_id,
        policy_id=policy_id,
        violation_status=status_filter,
        offset=offset,
        limit=limit,
    )
    return PolicyViolationListResponse(
        items=[_violation_to_response(v) for v in items], total=total
    )


@router.get("/violations/{violation_id}", response_model=PolicyViolationResponse)
async def get_policy_violation(
    violation_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_READ))
    ],
    service: ComplianceService = Depends(get_compliance_service),
) -> PolicyViolationResponse:
    violation = await service.get_violation(org_context.organization_id, violation_id)
    return _violation_to_response(violation)


@router.post("/violations/{violation_id}/retry", response_model=PolicyViolationResponse)
async def retry_violation_remediation(
    violation_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_REMEDIATION_MANAGE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ComplianceService = Depends(get_compliance_service),
) -> PolicyViolationResponse:
    violation = await service.retry_remediation(
        org_context.organization_id, violation_id, actor_user_id=current_user.user_id
    )
    return _violation_to_response(violation)


@router.post("/violations/{violation_id}/resolve", response_model=PolicyViolationResponse)
async def resolve_policy_violation(
    violation_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_REMEDIATION_MANAGE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ComplianceService = Depends(get_compliance_service),
) -> PolicyViolationResponse:
    violation = await service.resolve_violation(
        org_context.organization_id, violation_id, actor_user_id=current_user.user_id
    )
    return _violation_to_response(violation)


@router.post(
    "/violations/{violation_id}/disable-remediation", response_model=PolicyViolationResponse
)
async def disable_violation_remediation(
    violation_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_REMEDIATION_MANAGE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ComplianceService = Depends(get_compliance_service),
) -> PolicyViolationResponse:
    violation = await service.set_remediation_disabled(
        org_context.organization_id, violation_id, disabled=True, actor_user_id=current_user.user_id
    )
    return _violation_to_response(violation)


@router.post(
    "/violations/{violation_id}/enable-remediation", response_model=PolicyViolationResponse
)
async def enable_violation_remediation(
    violation_id: uuid.UUID,
    org_context: Annotated[
        OrganizationContext, Depends(require_compliance_permission(p.COMPLIANCE_REMEDIATION_MANAGE))
    ],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: ComplianceService = Depends(get_compliance_service),
) -> PolicyViolationResponse:
    violation = await service.set_remediation_disabled(
        org_context.organization_id,
        violation_id,
        disabled=False,
        actor_user_id=current_user.user_id,
    )
    return _violation_to_response(violation)
