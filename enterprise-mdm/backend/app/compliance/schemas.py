"""Pydantic schemas for the compliance API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.compliance.models import ComplianceStatus, ViolationStatus
from app.compliance.rules import RuleOutcome


class RuleResultResponse(BaseModel):
    code: str
    outcome: RuleOutcome
    message: str
    expected: str | None = None
    actual: str | None = None


class DeviceComplianceResponse(BaseModel):
    device_id: uuid.UUID
    organization_id: uuid.UUID
    status: ComplianceStatus
    policy_id: uuid.UUID | None
    policy_version_id: uuid.UUID | None
    evaluated_at: datetime | None
    details: list[RuleResultResponse]


class DeviceComplianceListResponse(BaseModel):
    items: list[DeviceComplianceResponse]
    total: int


class ComplianceSummaryResponse(BaseModel):
    total_devices: int
    compliant: int
    non_compliant: int
    unknown: int
    not_evaluated: int


# ---------------------------------------------------------------------------
# Policy violations (Phase 13)
# ---------------------------------------------------------------------------


class PolicyViolationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    policy_id: uuid.UUID
    policy_version_id: uuid.UUID | None
    rule_code: str
    status: ViolationStatus
    expected_value: str | None
    current_value: str | None
    message: str
    detected_at: datetime
    last_seen_at: datetime
    resolved_at: datetime | None
    resolution_reason: str | None
    remediation_attempts: int
    last_remediation_command_id: uuid.UUID | None
    last_remediation_at: datetime | None
    automated_remediation_disabled: bool
    created_at: datetime
    updated_at: datetime


class PolicyViolationListResponse(BaseModel):
    items: list[PolicyViolationResponse]
    total: int
