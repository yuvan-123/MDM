"""Background-job admin API (Phase 8 read, Phase 10 retry + traceability).

  GET  /jobs                [org] job.read (paginated,
                             ?job_type=&status=&command_id=)
  GET  /jobs/{job_id}        [org] job.read
  POST /jobs/{job_id}/retry  [org] job.retry

Flat, header-scoped resource (X-Organization-ID) like devices/policies/
compliance — see app/organizations/dependencies.py. `command_id` on the
list endpoint is command -> job traceability (Phase 10 §15): translates to
the exact dedup_key a COMMAND_DISPATCH job for that command would carry,
so an operator never needs to know that internal convention. `payload` is
returned as-is — every job type's payload is documented as containing only
UUIDs, scope enums, and pagination cursors, never a secret (see
app/jobs/models.py). Retry is deliberately gated by a separate `job.retry`
permission from `job.read` — seeing a failed job doesn't imply the
authority to re-trigger it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.jobs.dependencies import get_job_service, require_job_permission
from app.jobs.models import JobStatus, JobType
from app.jobs.schemas import BackgroundJobListResponse, BackgroundJobResponse
from app.jobs.service import JobService
from app.organizations.dependencies import OrganizationContext
from app.rbac import permissions as p
from app.rbac.dependencies import CurrentUser, get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])

MAX_PAGE_SIZE = 100


@router.get("", response_model=BackgroundJobListResponse)
async def list_jobs(
    org_context: Annotated[OrganizationContext, Depends(require_job_permission(p.JOB_READ))],
    job_type: JobType | None = Query(default=None),
    job_status: JobStatus | None = Query(default=None, alias="status"),
    command_id: uuid.UUID | None = Query(
        default=None, description="Filter to the COMMAND_DISPATCH job(s) for this DeviceCommand."
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    service: JobService = Depends(get_job_service),
) -> BackgroundJobListResponse:
    jobs, total = await service.list_jobs(
        org_context.organization_id,
        job_type=job_type,
        job_status=job_status,
        command_id=command_id,
        offset=offset,
        limit=limit,
    )
    return BackgroundJobListResponse(
        items=[BackgroundJobResponse.model_validate(j) for j in jobs], total=total
    )


@router.get("/{job_id}", response_model=BackgroundJobResponse)
async def get_job(
    job_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_job_permission(p.JOB_READ))],
    service: JobService = Depends(get_job_service),
) -> BackgroundJobResponse:
    job = await service.get_job(org_context.organization_id, job_id)
    return BackgroundJobResponse.model_validate(job)


@router.post("/{job_id}/retry", response_model=BackgroundJobResponse)
async def retry_job(
    job_id: uuid.UUID,
    org_context: Annotated[OrganizationContext, Depends(require_job_permission(p.JOB_RETRY))],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: JobService = Depends(get_job_service),
) -> BackgroundJobResponse:
    job = await service.retry_job(
        org_context.organization_id, job_id, actor_user_id=current_user.user_id
    )
    return BackgroundJobResponse.model_validate(job)
