"""Pydantic schemas for the device command API.

One request schema per command type rather than a single generic
`{"command_type": ..., "payload": ...}` body — matches the router's
one-route-per-verb design (see app/commands/router.py's module docstring)
and lets wipe carry stricter validation (`confirm`, required
`idempotency_key`) than the others without a runtime branch.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.commands.models import DeviceCommandStatus, DeviceCommandType

# ---------------------------------------------------------------------------
# Issue-command requests
# ---------------------------------------------------------------------------


class IssueLockCommandRequest(BaseModel):
    message: str | None = Field(
        default=None, max_length=500, description="Optional message shown on the lock screen."
    )
    idempotency_key: str | None = Field(default=None, max_length=255)


class IssueRebootCommandRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=255)


class IssueRefreshInfoCommandRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=255)


class IssuePolicySyncCommandRequest(BaseModel):
    idempotency_key: str | None = Field(default=None, max_length=255)


class IssueWipeCommandRequest(BaseModel):
    """Wipe carries extra safeguards not present on any other command type —
    see docs/security/phase-6-security-review.md: `confirm` must be
    explicitly `True` (an empty/minimal body can never trigger a wipe), and
    `idempotency_key` is REQUIRED (optional everywhere else) so a client
    retry can never double-dispatch a destructive command."""

    confirm: Literal[True] = Field(
        description="Must be explicitly true — a wipe cannot be issued without confirmation."
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=255,
        description="Required for wipe specifically, so a retried request never re-dispatches.",
    )


class IssueUnenrollCommandRequest(BaseModel):
    """Phase 12: same safeguard shape as wipe — unenrollment is at least as
    destructive to this codebase's ability to manage the device (it removes
    the provider link and revokes the device's credential; see
    CommandService._dispatch's UNENROLL handling)."""

    confirm: Literal[True] = Field(
        description="Must be explicitly true — unenrollment cannot be issued without confirmation."
    )
    idempotency_key: str = Field(
        min_length=1,
        max_length=255,
        description="Required for unenroll specifically, so a retried request never "
        "re-dispatches.",
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class DeviceCommandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    command_type: DeviceCommandType
    status: DeviceCommandStatus
    payload: dict[str, Any] | None
    provider_name: str
    provider_command_id: str | None
    result: dict[str, Any] | None
    error_code: str | None
    error_message: str | None
    requested_by: uuid.UUID | None
    cancelled_by: uuid.UUID | None
    cancelled_at: datetime | None
    dispatched_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime
    created_at: datetime


class DeviceCommandListResponse(BaseModel):
    items: list[DeviceCommandResponse]
    total: int


class DeviceCommandCapabilitiesResponse(BaseModel):
    """Phase 12: what `GET /devices/{id}/commands/capabilities` returns —
    lets a caller know which command types are actually issuable for this
    device BEFORE attempting one. See CommandService.get_supported_command_types."""

    provider_name: str
    provider_available: bool = Field(
        description="False when the provider itself couldn't be constructed "
        "(e.g. real credentials missing) — supported_command_types is empty "
        "in that case, not a guess."
    )
    supported_command_types: list[DeviceCommandType]
