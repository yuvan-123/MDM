"""Pydantic schemas for the provider-device-link admin API (Phase 10)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.provider_events.models import ProviderDeviceLinkSource


class CreateProviderDeviceLinkRequest(BaseModel):
    device_id: uuid.UUID
    provider_name: str = Field(min_length=1, max_length=50)
    provider_device_id: str = Field(min_length=1, max_length=255)
    apple_push_token: str | None = Field(default=None, max_length=255)


class UpdateProviderDeviceLinkRequest(BaseModel):
    """Only the mapping's own identifiers may change — `organization_id`,
    `device_id`, and `provider_name` together define *which* mapping this
    is; re-pointing one of those is a new link (delete + create), not an
    update, so they aren't fields here."""

    provider_device_id: str | None = Field(default=None, min_length=1, max_length=255)
    apple_push_token: str | None = Field(default=None, max_length=255)


class ProviderDeviceLinkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID
    provider_name: str
    provider_device_id: str
    apple_push_token: str | None
    link_source: ProviderDeviceLinkSource
    created_at: datetime
    updated_at: datetime


class ProviderDeviceLinkListResponse(BaseModel):
    items: list[ProviderDeviceLinkResponse]
    total: int
