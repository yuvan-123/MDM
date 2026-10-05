"""Pydantic schemas for the policy API.

`PolicyConfiguration` is the typed shape of the JSON blob stored on
PolicyVersion.configuration (STEP 6). Every field in every category is
optional — an absent field means "this policy does not constrain that
setting," not "require the default." This is what lets
app/compliance/rules.py add new checks later without a schema-breaking
migration: an old PolicyVersion simply has that field absent/None, and
the corresponding rule reports UNKNOWN rather than failing to parse.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.policies.models import PolicyScopeType, PolicyStatus

# ---------------------------------------------------------------------------
# Configuration categories (STEP 6)
# ---------------------------------------------------------------------------


class PasswordPolicyConfig(BaseModel):
    minimum_length: int | None = Field(default=None, ge=1, le=128)
    require_complexity: bool | None = None
    max_age_days: int | None = Field(default=None, ge=1)
    max_failed_attempts: int | None = Field(default=None, ge=1)


class DeviceSecurityPolicyConfig(BaseModel):
    require_encryption: bool | None = None
    require_screen_lock: bool | None = None
    minimum_os_version: str | None = Field(default=None, max_length=50)
    block_rooted_jailbroken: bool | None = None


class ApplicationPolicyConfig(BaseModel):
    allowed_app_ids: list[str] | None = None
    denied_app_ids: list[str] | None = None
    block_unapproved_installs: bool | None = None


class NetworkPolicyConfig(BaseModel):
    require_wifi: bool | None = None
    allowed_ssids: list[str] | None = None
    require_vpn: bool | None = None


class CompliancePolicyConfig(BaseModel):
    minimum_client_version: str | None = Field(default=None, max_length=50)
    grace_period_days: int | None = Field(default=None, ge=0)


class RemediationPolicyConfig(BaseModel):
    """Phase 13: what to do when this policy is violated.

    `command_type` is deliberately a closed allowlist, not the full
    `DeviceCommandType` — WIPE and UNENROLL are structurally impossible to
    configure here (a 422 at policy-save time, not a runtime safeguard
    someone could get wrong later), matching the brief's "automated
    remediation must not wipe devices by default" and "prefer NOT
    implementing automatic destructive remediation." REBOOT is excluded
    too — disruptive to an in-use device and not among the safe examples
    the brief names (refresh, compliance check, lock, sync)."""

    mode: Literal["NONE", "NOTIFY", "COMMAND"] = "NONE"
    command_type: Literal["REFRESH_INFO", "POLICY_SYNC", "LOCK"] | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _command_type_required_for_command_mode(self) -> RemediationPolicyConfig:
        if self.mode == "COMMAND" and self.command_type is None:
            raise ValueError("command_type is required when remediation mode is COMMAND.")
        if self.mode != "COMMAND" and self.command_type is not None:
            raise ValueError("command_type is only meaningful when remediation mode is COMMAND.")
        return self


class PolicyConfiguration(BaseModel):
    password: PasswordPolicyConfig | None = None
    device_security: DeviceSecurityPolicyConfig | None = None
    application: ApplicationPolicyConfig | None = None
    network: NetworkPolicyConfig | None = None
    compliance: CompliancePolicyConfig | None = None
    remediation: RemediationPolicyConfig | None = None


# ---------------------------------------------------------------------------
# Policy CRUD
# ---------------------------------------------------------------------------


class CreatePolicyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    configuration: PolicyConfiguration = Field(default_factory=PolicyConfiguration)


class UpdatePolicyRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    status: PolicyStatus | None = Field(
        default=None,
        description="DRAFT <-> ACTIVE only. ARCHIVED is reached only via DELETE.",
    )
    configuration: PolicyConfiguration | None = Field(
        default=None,
        description="If provided, creates a new immutable PolicyVersion and "
        "repoints current_version — never mutates a past version.",
    )


class PolicyVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    policy_id: uuid.UUID
    version: int
    configuration: PolicyConfiguration
    created_at: datetime
    created_by: uuid.UUID | None


class PolicyResponse(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    status: PolicyStatus
    current_version: PolicyVersionResponse | None
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class PolicyListResponse(BaseModel):
    items: list[PolicyResponse]
    total: int


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


class AssignPolicyRequest(BaseModel):
    scope_type: PolicyScopeType
    scope_id: uuid.UUID


class PolicyAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    policy_id: uuid.UUID
    scope_type: PolicyScopeType
    scope_id: uuid.UUID
    created_at: datetime
    created_by: uuid.UUID | None
