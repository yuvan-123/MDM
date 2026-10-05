"""Phase 7 background-job enqueue/claim/retry unit tests — exercises
JobService and BackgroundJobRepository directly (matches the codebase's
existing style for repository-level tests; no HTTP router exists for this
module by design, see app/jobs/service.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import JobStatus, JobType
from app.jobs.retry import is_transient
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.base.errors import PlatformProviderUnavailableError
from app.policies.models import PolicyScopeType
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository


def _service(db_session: AsyncSession) -> JobService:
    return JobService(BackgroundJobRepository(db_session), AuditRepository(db_session))


@pytest.mark.asyncio
async def test_enqueue_compliance_evaluation_creates_pending_job(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    device_id = uuid.uuid4()
    job = await _service(db_session).enqueue_compliance_evaluation(organization_a.id, device_id)
    assert job is not None
    assert job.job_type == JobType.COMPLIANCE_EVALUATION
    assert job.status == JobStatus.PENDING
    assert job.payload == {"device_id": str(device_id)}
    assert job.dedup_key == f"compliance_evaluation:{device_id}"


@pytest.mark.asyncio
async def test_enqueue_compliance_evaluation_dedupes_while_pending(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    """Repeated triggers for the same device (e.g. a burst of heartbeats)
    must collapse into one queued evaluation, not one row per trigger."""
    device_id = uuid.uuid4()
    service = _service(db_session)

    first = await service.enqueue_compliance_evaluation(organization_a.id, device_id)
    second = await service.enqueue_compliance_evaluation(organization_a.id, device_id)

    assert first is not None
    assert second is None

    repo = BackgroundJobRepository(db_session)
    still_active = await repo.get_active_by_dedup_key(
        organization_a.id, JobType.COMPLIANCE_EVALUATION, f"compliance_evaluation:{device_id}"
    )
    assert still_active is not None
    assert still_active.id == first.id


@pytest.mark.asyncio
async def test_enqueue_compliance_evaluation_allows_new_job_after_completion(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    """Dedup must only suppress *active* (PENDING/CLAIMED) duplicates — once
    the prior job reaches a terminal state, a fresh trigger schedules a new
    one instead of being silently dropped forever."""
    device_id = uuid.uuid4()
    service = _service(db_session)
    repo = BackgroundJobRepository(db_session)

    first = await service.enqueue_compliance_evaluation(organization_a.id, device_id)
    assert first is not None
    claimed = await repo.claim_next()
    assert claimed is not None and claimed.id == first.id
    await repo.mark_completed(claimed)

    second = await service.enqueue_compliance_evaluation(organization_a.id, device_id)
    assert second is not None
    assert second.id != first.id


@pytest.mark.asyncio
async def test_enqueue_policy_reevaluation_device_scope_schedules_compliance_job(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    device_id = uuid.uuid4()
    job = await _service(db_session).enqueue_policy_reevaluation(
        organization_a.id, scope_type=PolicyScopeType.DEVICE, scope_id=device_id
    )
    assert job is not None
    assert job.job_type == JobType.COMPLIANCE_EVALUATION
    assert job.payload == {"device_id": str(device_id)}


@pytest.mark.asyncio
async def test_enqueue_policy_reevaluation_group_scope_schedules_fanout(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    group_id = uuid.uuid4()
    job = await _service(db_session).enqueue_policy_reevaluation(
        organization_a.id, scope_type=PolicyScopeType.DEVICE_GROUP, scope_id=group_id
    )
    assert job is not None
    assert job.job_type == JobType.POLICY_REEVALUATION_FANOUT
    assert job.payload == {
        "scope_type": "DEVICE_GROUP",
        "scope_id": str(group_id),
        "cursor_offset": 0,
        "page_count": 0,
    }
    assert job.dedup_key == f"policy_reevaluation_fanout:DEVICE_GROUP:{group_id}"


@pytest.mark.asyncio
async def test_claim_next_respects_scheduled_at(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    repo = BackgroundJobRepository(db_session)
    future_job = await repo.create(
        organization_a.id,
        job_type=JobType.COMPLIANCE_EVALUATION,
        status=JobStatus.PENDING,
        payload={"device_id": str(uuid.uuid4())},
        dedup_key=None,
        attempts=0,
        scheduled_at=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    claimed = await repo.claim_next()
    assert claimed is None

    due_job = await repo.create(
        organization_a.id,
        job_type=JobType.COMPLIANCE_EVALUATION,
        status=JobStatus.PENDING,
        payload={"device_id": str(uuid.uuid4())},
        dedup_key=None,
        attempts=0,
        scheduled_at=datetime.now(tz=UTC),
    )
    claimed = await repo.claim_next()
    assert claimed is not None
    assert claimed.id == due_job.id
    assert claimed.status == JobStatus.CLAIMED
    assert claimed.attempts == 1
    assert future_job.status == JobStatus.PENDING  # left untouched


@pytest.mark.asyncio
async def test_mark_failed_retries_transient_then_gives_up(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    repo = BackgroundJobRepository(db_session)
    await repo.create(
        organization_a.id,
        job_type=JobType.COMPLIANCE_EVALUATION,
        status=JobStatus.PENDING,
        payload={"device_id": str(uuid.uuid4())},
        dedup_key=None,
        attempts=0,
        max_attempts=2,
        scheduled_at=datetime.now(tz=UTC),
    )

    claimed = await repo.claim_next()
    assert claimed is not None
    assert claimed.attempts == 1
    retried = await repo.mark_failed(claimed, error="boom", retry=True)
    assert retried.status == JobStatus.PENDING
    # SQLite drops tzinfo on DateTime(timezone=True) round-trip even though
    # Postgres would not — normalize before comparing, same fix pattern as
    # app/commands/service.py::_effective_command_status.
    scheduled_at = retried.scheduled_at
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=UTC)
    assert scheduled_at > datetime.now(tz=UTC)
    assert retried.last_error == "boom"

    retried.scheduled_at = datetime.now(tz=UTC)  # force it eligible for the second attempt
    await db_session.flush()
    claimed_again = await repo.claim_next()
    assert claimed_again is not None
    assert claimed_again.attempts == 2
    final = await repo.mark_failed(claimed_again, error="boom again", retry=True)
    assert final.status == JobStatus.FAILED  # attempts (2) == max_attempts (2): no more retries
    assert final.completed_at is not None


@pytest.mark.asyncio
async def test_mark_failed_permanent_error_skips_retry(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    repo = BackgroundJobRepository(db_session)
    await repo.create(
        organization_a.id,
        job_type=JobType.COMPLIANCE_EVALUATION,
        status=JobStatus.PENDING,
        payload={"device_id": str(uuid.uuid4())},
        dedup_key=None,
        attempts=0,
        max_attempts=5,
        scheduled_at=datetime.now(tz=UTC),
    )
    claimed = await repo.claim_next()
    assert claimed is not None
    failed = await repo.mark_failed(claimed, error="DEVICE_NOT_FOUND", retry=False)
    assert failed.status == JobStatus.FAILED


def test_is_transient_classifies_provider_unavailable_as_retryable() -> None:
    assert is_transient(PlatformProviderUnavailableError("android", "no credentials")) is True


def test_is_transient_classifies_unrecognized_apperror_as_permanent() -> None:
    from app.core.errors import AppError

    assert is_transient(AppError(code="DEVICE_NOT_FOUND", message="gone")) is False


def test_is_transient_classifies_unexpected_exception_as_retryable() -> None:
    assert is_transient(RuntimeError("unexpected")) is True
