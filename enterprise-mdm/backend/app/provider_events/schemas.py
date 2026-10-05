"""Pydantic schemas for the provider-event admin API (Phase 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.provider_events.models import ProviderEventProcessingStatus


class ProviderEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID | None
    device_id: uuid.UUID | None
    provider_name: str
    provider_event_id: str
    event_type: str
    payload: dict[str, Any]
    received_at: datetime
    processing_status: ProviderEventProcessingStatus
    processed_at: datetime | None
    processing_error: str | None


class ProviderEventListResponse(BaseModel):
    items: list[ProviderEventResponse]
    total: int
