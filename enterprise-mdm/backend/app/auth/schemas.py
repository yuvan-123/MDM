"""Pydantic schemas for authentication requests and responses.

Rules:
- NEVER include password_hash, refresh_token_hash, reset_token_hash, or
  raw tokens in any response schema.
- All request schemas perform validation (email normalization, password policy).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=12, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Response Schemas
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    """Public representation of a User. Never contains password_hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    full_name: str
    status: str
    is_email_verified: bool
    last_login_at: datetime | None
    created_at: datetime


class LoginResponse(BaseModel):
    """Returned on successful login when cookies cannot be used (e.g. CLI/API clients).

    For browser clients, tokens are delivered via HttpOnly cookies and this
    body provides user summary + CSRF token.
    """

    user: UserResponse
    csrf_token: str
    # Access/refresh tokens included in body for non-browser Bearer clients
    access_token: str | None = None
    token_type: str = "Bearer"


class SessionResponse(BaseModel):
    """Representation of an active user session."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime
    ip_address: str | None
    user_agent: str | None
    device_identifier: str | None
    is_current: bool = False


class MeResponse(BaseModel):
    """Current authenticated user information including effective permissions."""

    user: UserResponse
    session_id: uuid.UUID
    permissions: list[str]
