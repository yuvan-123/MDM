"""Background job enqueue + admin-read/retry service (Phase 7 enqueue,
Phase 8 read, Phase 10 manual retry).

Constructs no raw SQL/ORM queries — delegates strictly to
BackgroundJobRepository and AuditRepository. Processing/claiming lives in
app/workers/loop.py; enqueue/read never mutate a job's processing state —
only `retry_job` does, and only for an already-FAILED job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.jobs.models import BackgroundJob, JobStatus, JobType
from app.policies.models import PolicyScopeType
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository


class JobService:
    def __init__(self, job_repo: BackgroundJobRepository, audit_repo: AuditRepository) -> None:
        self._job_repo = job_repo
        self._audit_repo = audit_repo

    async def _enqueue(
        self,
        organization_id: uuid.UUID,
        job_type: JobType,
        payload: dict[str, object],
        *,
        dedup_key: str | None,
        delay_seconds: float = 0,
    ) -> BackgroundJob | None:
        if dedup_key is not None:
            existing = await self._job_repo.get_active_by_dedup_key(
                organization_id, job_type, dedup_key
            )
            if existing is not None:
                # A PENDING or CLAIMED job already covers this exact target
                # — collapsing repeated triggers (e.g. a burst of device
                # heartbeats) into one queued evaluation instead of one row
                # per trigger is the entire point of dedup_key.
                return None
        scheduled_at = datetime.now(tz=UTC) + timedelta(seconds=delay_seconds)
        return await self._job_repo.create(
            organization_id,
            job_type=job_type,
            status=JobStatus.PENDING,
            payload=payload,
            dedup_key=dedup_key,
            attempts=0,
            scheduled_at=scheduled_at,
        )

    async def enqueue_compliance_evaluation(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> BackgroundJob | None:
        return await self._enqueue(
            organization_id,
            JobType.COMPLIANCE_EVALUATION,
            {"device_id": str(device_id)},
            dedup_key=f"compliance_evaluation:{device_id}",
        )

    async def enqueue_command_dispatch(
        self, organization_id: uuid.UUID, device_command_id: uuid.UUID
    ) -> BackgroundJob | None:
        """Phase 8: decouples DeviceCommand creation from provider dispatch
        — see app/commands/service.py::CommandService.create_command /
        process_dispatch_job. dedup_key is the command's own id: a command
        has exactly one legitimate dispatch attempt chain, so at most one
        active COMMAND_DISPATCH job should ever exist for it."""
        return await self._enqueue(
            organization_id,
            JobType.COMMAND_DISPATCH,
            {"device_command_id": str(device_command_id)},
            dedup_key=f"command_dispatch:{device_command_id}",
        )

    async def enqueue_compliance_remediation(
        self, organization_id: uuid.UUID, violation_id: uuid.UUID, *, command_type: str
    ) -> BackgroundJob | None:
        """Phase 13. dedup_key is the violation's own id — a violation has
        at most one active remediation attempt in flight at a time, the
        same one-active-job-per-target invariant enqueue_command_dispatch
        already establishes for commands. `command_type` is snapshotted
        into the payload from the policy's remediation config AT THE
        MOMENT evaluate_device decided to remediate — deliberately not
        re-read from the policy when the job runs, mirroring
        DeviceCommand.provider_name's own "recorded at dispatch time, even
        if config later changes" precedent."""
        return await self._enqueue(
            organization_id,
            JobType.COMPLIANCE_REMEDIATION,
            {"violation_id": str(violation_id), "command_type": command_type},
            dedup_key=f"compliance_remediation:{violation_id}",
        )

    async def enqueue_policy_reevaluation(
        self,
        organization_id: uuid.UUID,
        *,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> BackgroundJob | None:
        """DEVICE scope resolves to exactly one device, so it schedules a
        COMPLIANCE_EVALUATION job directly — no fanout needed. Every wider
        scope schedules the first page of a POLICY_REEVALUATION_FANOUT
        chain (see app/workers/loop.py)."""
        if scope_type == PolicyScopeType.DEVICE:
            return await self.enqueue_compliance_evaluation(organization_id, scope_id)
        return await self._enqueue(
            organization_id,
            JobType.POLICY_REEVALUATION_FANOUT,
            {
                "scope_type": scope_type.value,
                "scope_id": str(scope_id),
                "cursor_offset": 0,
                "page_count": 0,
            },
            dedup_key=f"policy_reevaluation_fanout:{scope_type.value}:{scope_id}",
        )

    # ------------------------------------------------------------------
    # Read (Phase 8 admin API)
    # ------------------------------------------------------------------

    async def get_job(self, organization_id: uuid.UUID, job_id: uuid.UUID) -> BackgroundJob:
        job = await self._job_repo.get_for_organization(organization_id, job_id)
        if job is None:
            raise AppError(
                code="JOB_NOT_FOUND",
                message="Background job not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return job

    async def list_jobs(
        self,
        organization_id: uuid.UUID,
        *,
        job_type: JobType | None = None,
        job_status: JobStatus | None = None,
        command_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[BackgroundJob], int]:
        # Phase 10 §15 (command -> job traceability): a COMMAND_DISPATCH
        # job's dedup_key is always exactly f"command_dispatch:{command_id}"
        # (see enqueue_command_dispatch) — translating the human-meaningful
        # `command_id` filter into that exact string here means the caller
        # never needs to know the dedup_key convention itself.
        dedup_key = f"command_dispatch:{command_id}" if command_id is not None else None
        jobs = await self._job_repo.list_for_organization(
            organization_id,
            job_type=job_type,
            status=job_status,
            dedup_key=dedup_key,
            offset=offset,
            limit=limit,
        )
        total = await self._job_repo.count_for_organization(
            organization_id, job_type=job_type, status=job_status, dedup_key=dedup_key
        )
        return jobs, total

    # ------------------------------------------------------------------
    # Manual retry (Phase 10 admin API)
    # ------------------------------------------------------------------

    async def retry_job(
        self, organization_id: uuid.UUID, job_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> BackgroundJob:
        """Only a FAILED job may be manually retried — not PENDING/CLAIMED
        (already active) and not COMPLETED (retrying a successful job
        makes no sense and risks a real double-execution for a
        COMMAND_DISPATCH job specifically). See
        `BackgroundJobRepository.retry` for why `attempts`/`last_error`
        are preserved, not reset."""
        job = await self.get_job(organization_id, job_id)
        if job.status != JobStatus.FAILED:
            raise AppError(
                code="JOB_NOT_RETRYABLE",
                message=f"Only a FAILED job can be manually retried (this job is "
                f"{job.status.value}).",
                status_code=status.HTTP_409_CONFLICT,
            )
        job = await self._job_repo.retry(job)
        await self._audit_repo.log_event(
            event_type=AuditEventType.BACKGROUND_JOB_RETRIED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"job_id": str(job.id), "job_type": job.job_type.value},
        )
        return job
