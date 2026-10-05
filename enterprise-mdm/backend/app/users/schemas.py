"""Pydantic schemas for User management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.users.models import UserStatus


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    initial_role: str | None = Field(
        default=None,
        description="Optional role to assign on creation (e.g. 'ORGANIZATION_ADMIN').",
    )


class UpdateUserRequest(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)


class UpdateUserStatusRequest(BaseModel):
    status: UserStatus


class UserRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role_name: str
    granted_at: datetime
    granted_by: uuid.UUID | None
    organization_id: uuid.UUID | None


class UserDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    status: UserStatus
    is_email_verified: bool
    last_login_at: datetime | None
    failed_login_attempts: int
    locked_until: datetime | None
    created_at: datetime
    updated_at: datetime
    roles: list[UserRoleResponse] = []


class AssignRoleRequest(BaseModel):
    role_name: str = Field(min_length=1)
    organization_id: uuid.UUID | None = Field(
        default=None,
        description="NULL for platform role; set to org ID in Phase 3.",
    )
