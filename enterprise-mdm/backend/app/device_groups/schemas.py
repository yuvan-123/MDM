"""Pydantic schemas for the device group API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateDeviceGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class UpdateDeviceGroupRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class DeviceGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    description: str | None
    device_count: int = 0
    created_at: datetime
    updated_at: datetime


class DeviceGroupListResponse(BaseModel):
    items: list[DeviceGroupResponse]
    total: int
