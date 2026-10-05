"""Pydantic schemas for the background-job admin API (Phase 8)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.jobs.models import JobStatus, JobType


class BackgroundJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    job_type: JobType
    status: JobStatus
    payload: dict[str, Any]
    dedup_key: str | None
    attempts: int
    max_attempts: int
    scheduled_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class BackgroundJobListResponse(BaseModel):
    items: list[BackgroundJobResponse]
    total: int
