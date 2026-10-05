"""BackgroundJob repository — the ONLY layer that constructs SQLAlchemy
queries for this model.

Unlike every other Phase 4-6 repository, `claim_next` deliberately has NO
`organization_id` parameter: the worker loop is a system-level process
serving every tenant from one queue, not an admin-facing, tenant-scoped
code path — the same category of exception as
DeviceRepository.get_by_id_unscoped and
DeviceCommandRepository.list_expired_sent_unscoped.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import BackgroundJob, JobStatus, JobType

_ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.CLAIMED)

# Phase 8: a CLAIMED job whose worker crashed before completing it must not
# stay stuck forever — see §20 of docs/architecture/background-jobs.md.
# There's no heartbeat mechanism (no live Postgres to prove one against
# genuinely), so recovery is a flat lease timeout computed from claimed_at
# rather than a stored expiry column: simpler, and sufficient for the
# single-process-crash scenario this dev environment can actually exercise.
JOB_LEASE_TIMEOUT = timedelta(minutes=10)


class BackgroundJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_by_dedup_key(
        self, organization_id: uuid.UUID, job_type: JobType, dedup_key: str
    ) -> BackgroundJob | None:
        result = await self._session.execute(
            select(BackgroundJob).where(
                BackgroundJob.organization_id == organization_id,
                BackgroundJob.job_type == job_type,
                BackgroundJob.dedup_key == dedup_key,
                BackgroundJob.status.in_(_ACTIVE_STATUSES),
            )
        )
        return result.scalar_one_or_none()

    async def get_for_organization(
        self, organization_id: uuid.UUID, job_id: uuid.UUID
    ) -> BackgroundJob | None:
        """Admin-API read (Phase 8) — org-scoped like every other Phase 4+
        resource. Unlike `claim_next`, this is a caller-facing lookup, so it
        follows the normal tenant-isolation rule rather than the
        system-level exception documented on the class."""
        result = await self._session.execute(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id, BackgroundJob.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        job_type: JobType | None = None,
        status: JobStatus | None = None,
        dedup_key: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[BackgroundJob]:
        query = select(BackgroundJob).where(BackgroundJob.organization_id == organization_id)
        if job_type is not None:
            query = query.where(BackgroundJob.job_type == job_type)
        if status is not None:
            query = query.where(BackgroundJob.status == status)
        if dedup_key is not None:
            # Phase 10: command -> job traceability. Not a uniqueness
            # guarantee (dedup_key is only enforced against *active* rows
            # at enqueue time — see JobService._enqueue), so this can
            # legitimately return more than one historical row; ordering by
            # created_at descending still puts the most relevant one first.
            query = query.where(BackgroundJob.dedup_key == dedup_key)
        result = await self._session.execute(
            query.order_by(BackgroundJob.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        job_type: JobType | None = None,
        status: JobStatus | None = None,
        dedup_key: str | None = None,
    ) -> int:
        query = (
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.organization_id == organization_id)
        )
        if job_type is not None:
            query = query.where(BackgroundJob.job_type == job_type)
        if status is not None:
            query = query.where(BackgroundJob.status == status)
        if dedup_key is not None:
            query = query.where(BackgroundJob.dedup_key == dedup_key)
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def create(self, organization_id: uuid.UUID, **fields: object) -> BackgroundJob:
        job = BackgroundJob(organization_id=organization_id, **fields)
        self._session.add(job)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def claim_next(self) -> BackgroundJob | None:
        """Claims the oldest-eligible job across every organization —
        either a due PENDING job, or a CLAIMED job whose lease has expired
        (its worker presumably crashed; see JOB_LEASE_TIMEOUT above).

        Uses `SELECT ... FOR UPDATE SKIP LOCKED` on Postgres so two workers
        racing this query can never both select the same row: the second
        worker's SELECT simply skips a row the first has already locked,
        rather than blocking on it and then re-reading stale data. SQLite
        (the test harness) does not support `SKIP LOCKED` — that dialect
        falls back to plain `FOR UPDATE`, which SQLite itself
        silently no-ops on (single-writer database, no concurrent workers
        possible in tests anyway). Genuine multi-worker safety is therefore
        proven by the Postgres-specific SQL construct being used at all, not
        by a test that can exercise real concurrent access — no live
        Postgres in this dev environment. See
        docs/architecture/background-jobs.md §8."""
        now = datetime.now(tz=UTC)
        lease_cutoff = now - JOB_LEASE_TIMEOUT
        query = (
            select(BackgroundJob)
            .where(
                or_(
                    (BackgroundJob.status == JobStatus.PENDING)
                    & (BackgroundJob.scheduled_at <= now),
                    (BackgroundJob.status == JobStatus.CLAIMED)
                    & (BackgroundJob.claimed_at <= lease_cutoff),
                )
            )
            .order_by(BackgroundJob.scheduled_at.asc())
            .limit(1)
        )
        if self._session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        else:
            query = query.with_for_update()

        result = await self._session.execute(query)
        job = result.scalar_one_or_none()
        if job is None:
            return None
        job.status = JobStatus.CLAIMED
        job.claimed_at = now
        job.attempts += 1
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_completed(self, job: BackgroundJob) -> BackgroundJob:
        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.now(tz=UTC)
        job.last_error = None
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def mark_failed(self, job: BackgroundJob, *, error: str, retry: bool) -> BackgroundJob:
        job.last_error = error[:2000]
        if retry and job.attempts < job.max_attempts:
            job.status = JobStatus.PENDING
            backoff_seconds = min(2**job.attempts, 300)
            job.scheduled_at = datetime.now(tz=UTC) + timedelta(seconds=backoff_seconds)
        else:
            job.status = JobStatus.FAILED
            job.completed_at = datetime.now(tz=UTC)
        await self._session.flush()
        await self._session.refresh(job)
        return job

    async def retry(self, job: BackgroundJob) -> BackgroundJob:
        """Phase 10: an administrator manually re-queuing a FAILED job.
        Deliberately does NOT reset `attempts` or clear `last_error` —
        those are historical record (Phase 10 §13: "do not reset
        historical failure information"); the next claim increments
        `attempts` again exactly like an automatic retry would, so a job
        that keeps failing still eventually re-exhausts `max_attempts`
        rather than being retriable forever. Caller (JobService) is
        responsible for verifying the job is actually FAILED first."""
        job.status = JobStatus.PENDING
        job.scheduled_at = datetime.now(tz=UTC)
        job.completed_at = None
        await self._session.flush()
        await self._session.refresh(job)
        return job
