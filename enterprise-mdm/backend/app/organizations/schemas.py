"""Pydantic schemas for organization/membership/invitation endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.organizations.models import InvitationStatus, MembershipStatus, OrganizationStatus

# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


class CreateOrganizationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class UpdateOrganizationRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


class UpdateOrganizationStatusRequest(BaseModel):
    status: OrganizationStatus


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


class MembershipRoleResponse(BaseModel):
    id: uuid.UUID
    role_name: str
    granted_at: datetime


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    user_email: str
    user_full_name: str
    status: MembershipStatus
    created_at: datetime
    roles: list[MembershipRoleResponse] = []


class UpdateMembershipStatusRequest(BaseModel):
    status: MembershipStatus


class AssignOrgRoleRequest(BaseModel):
    role_name: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


class InviteMemberRequest(BaseModel):
    email: EmailStr
    role_name: str = Field(min_length=1)


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    status: InvitationStatus
    created_at: datetime
    expires_at: datetime


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str | None = Field(default=None, min_length=12, max_length=128)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# Tenant context selection
# ---------------------------------------------------------------------------


class SelectOrganizationRequest(BaseModel):
    # Always re-validated server-side against real membership — never trusted as-is.
    organization_id: uuid.UUID


class OrganizationContextResponse(BaseModel):
    organization: OrganizationResponse
    membership_id: uuid.UUID | None
    is_platform_super_admin: bool
    permissions: list[str]
