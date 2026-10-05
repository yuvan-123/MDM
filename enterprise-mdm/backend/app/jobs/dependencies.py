"""FastAPI dependencies for the background-jobs module.

`require_job_permission` uses the same header-based
(X-Organization-ID) tenant-context pattern as devices/policies/compliance
— jobs are a flat, non-nested resource (see app/jobs/router.py)."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.jobs.service import JobService
from app.organizations.dependencies import require_current_organization_permission
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository

require_job_permission = require_current_organization_permission


def get_job_service(db: AsyncSession = Depends(get_db_session)) -> JobService:
    return JobService(job_repo=BackgroundJobRepository(db), audit_repo=AuditRepository(db))
