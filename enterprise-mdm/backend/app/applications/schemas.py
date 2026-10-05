"""Pydantic schemas for the application-management API (Phase 14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.applications.models import ApplicationDeploymentStatus, ApplicationRequirement
from app.devices.enums import DevicePlatform
from app.policies.models import PolicyScopeType

# ---------------------------------------------------------------------------
# Catalog CRUD
# ---------------------------------------------------------------------------


class CreateApplicationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform: DevicePlatform
    package_name: str | None = Field(default=None, max_length=255)
    bundle_identifier: str | None = Field(default=None, max_length=255)
    version: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    publisher: str | None = Field(default=None, max_length=200)
    managed: bool = True

    @model_validator(mode="after")
    def _identifier_matches_platform(self) -> CreateApplicationRequest:
        if self.platform == DevicePlatform.ANDROID:
            if not self.package_name or self.bundle_identifier:
                raise ValueError(
                    "ANDROID applications require package_name and must not set "
                    "bundle_identifier."
                )
        elif self.platform in (DevicePlatform.IOS, DevicePlatform.IPADOS):
            if not self.bundle_identifier or self.package_name:
                raise ValueError(
                    "IOS/IPADOS applications require bundle_identifier and must not set "
                    "package_name."
                )
        elif self.package_name or self.bundle_identifier:
            raise ValueError("OTHER platform applications must not set an identifier field.")
        return self


class UpdateApplicationRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=2000)
    publisher: str | None = Field(default=None, max_length=200)
    managed: bool | None = None


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    platform: DevicePlatform
    package_name: str | None
    bundle_identifier: str | None
    version: str | None
    description: str | None
    publisher: str | None
    managed: bool
    created_by: uuid.UUID | None
    updated_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


class AssignApplicationRequest(BaseModel):
    scope_type: PolicyScopeType
    scope_id: uuid.UUID
    requirement: ApplicationRequirement = ApplicationRequirement.OPTIONAL


class ApplicationAssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    application_id: uuid.UUID
    scope_type: PolicyScopeType
    scope_id: uuid.UUID
    requirement: ApplicationRequirement
    created_at: datetime
    created_by: uuid.UUID | None


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


class DeployApplicationRequest(BaseModel):
    """Bulk-capable per brief §13: exactly one target field must be set.
    `device_ids` deploys to each listed device independently — every
    device gets its own ApplicationDeployment row and its own
    DeviceCommand, never a single shared dispatch."""

    device_id: uuid.UUID | None = None
    device_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=500)
    device_group_id: uuid.UUID | None = None
    organization_wide: bool = False

    @model_validator(mode="after")
    def _exactly_one_target(self) -> DeployApplicationRequest:
        targets = [
            self.device_id is not None,
            self.device_ids is not None,
            self.device_group_id is not None,
            self.organization_wide,
        ]
        if sum(targets) != 1:
            raise ValueError(
                "Exactly one of device_id, device_ids, device_group_id, or "
                "organization_wide must be set."
            )
        return self


class ApplicationDeploymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    application_id: uuid.UUID
    assignment_id: uuid.UUID | None
    requirement: ApplicationRequirement
    status: ApplicationDeploymentStatus
    attempts: int
    last_command_id: uuid.UUID | None
    last_error: str | None
    installed_at: datetime | None
    removed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ApplicationDeploymentListResponse(BaseModel):
    items: list[ApplicationDeploymentResponse]
    total: int


class DeviceApplicationView(BaseModel):
    """One row of `GET /devices/{id}/applications` — an application
    resolved (via any assignment scope) as applicable to this device,
    joined with its current deployment status if one exists. Never
    fabricates an "installed" claim with no deployment/command signal
    behind it — see docs/architecture/phase-14.md §9."""

    application: ApplicationResponse
    requirement: ApplicationRequirement
    deployment: ApplicationDeploymentResponse | None


class DeviceApplicationsResponse(BaseModel):
    items: list[DeviceApplicationView]
