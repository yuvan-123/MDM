"""Postgres-backed background-job worker loop (Phase 7, extended Phase 8
with COMMAND_DISPATCH processing and FOR UPDATE SKIP LOCKED claiming).

No Celery — it is a declared-but-100%-unused `pyproject.toml` dependency;
`app/core/redis.py`'s own docstring defers it to "Phase 25"; the
architecture diagram shows it as a never-wired placeholder. Chosen instead:
this simple polling loop over `app/jobs/models.py::BackgroundJob`, mirroring
DeviceCommand's (Phase 6) auditable-row-per-unit-of-work conventions. See
docs/architecture/background-jobs.md for the full design writeup.

`run_worker_once` takes a session factory as a parameter (not the
module-level `AsyncSessionLocal`) specifically so tests can pass
`TestingSessionLocal` directly — fully testable without Postgres/Docker.
`scripts/worker.py` is the real CLI entrypoint.

Each cycle: the command-reconciliation sweep first (cheap, always runs),
then at most one background job claimed and processed to completion.
Genuine multi-worker concurrent-claim safety is unverified — no live
Postgres in this dev environment; see
BackgroundJobRepository.claim_next's docstring.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.applications.service import ApplicationService
from app.audit.models import AuditEventType
from app.commands.models import DeviceCommandType
from app.commands.service import CommandService
from app.compliance.models import ViolationStatus
from app.compliance.service import ComplianceService
from app.core.errors import AppError
from app.core.logging import get_logger
from app.devices.models import Device
from app.jobs.models import BackgroundJob, JobStatus, JobType
from app.jobs.retry import is_transient
from app.jobs.service import JobService
from app.platform.registry import PlatformProviderRegistry
from app.policies.models import PolicyScopeType
from app.policies.service import PolicyResolutionService
from app.repositories.application_assignment_repository import ApplicationAssignmentRepository
from app.repositories.application_deployment_repository import ApplicationDeploymentRepository
from app.repositories.application_repository import ApplicationRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.compliance_repository import ComplianceRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.policy_assignment_repository import PolicyAssignmentRepository
from app.repositories.policy_repository import PolicyRepository
from app.repositories.policy_violation_repository import PolicyViolationRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

logger = get_logger(__name__)

# Devices re-evaluated per POLICY_REEVALUATION_FANOUT job page, and the page
# count at which a chain stops re-enqueueing itself (a soft cap of ~100k
# devices per policy-change trigger — a scope larger than that will need a
# follow-up trigger, an accepted Phase 7 limitation).
_FANOUT_PAGE_SIZE = 200
_FANOUT_MAX_PAGES = 500


def _build_job_service(session: AsyncSession) -> JobService:
    return JobService(BackgroundJobRepository(session), AuditRepository(session))


def _build_command_service(session: AsyncSession) -> CommandService:
    return CommandService(
        DeviceCommandRepository(session),
        DeviceRepository(session),
        PlatformProviderRegistry(),
        AuditRepository(session),
        JobService(BackgroundJobRepository(session), AuditRepository(session)),
        ProviderDeviceLinkRepository(session),
    )


def _build_application_service(session: AsyncSession) -> ApplicationService:
    return ApplicationService(
        application_repo=ApplicationRepository(session),
        assignment_repo=ApplicationAssignmentRepository(session),
        deployment_repo=ApplicationDeploymentRepository(session),
        device_repo=DeviceRepository(session),
        device_group_repo=DeviceGroupRepository(session),
        membership_repo=MembershipRepository(session),
        command_repo=DeviceCommandRepository(session),
        command_service=_build_command_service(session),
        audit_repo=AuditRepository(session),
    )


def _build_compliance_service(session: AsyncSession) -> ComplianceService:
    return ComplianceService(
        ComplianceRepository(session),
        DeviceRepository(session),
        PolicyResolutionService(
            PolicyRepository(session),
            PolicyAssignmentRepository(session),
            DeviceGroupRepository(session),
        ),
        AuditRepository(session),
        PolicyViolationRepository(session),
        DeviceCommandRepository(session),
        _build_job_service(session),
        PolicyRepository(session),
        _build_application_service(session),
    )


async def _process_compliance_evaluation(session: AsyncSession, job: BackgroundJob) -> None:
    device_id = uuid.UUID(str(job.payload["device_id"]))
    await _build_compliance_service(session).evaluate_device(
        job.organization_id, device_id, actor_user_id=None
    )


async def _process_compliance_remediation(session: AsyncSession, job: BackgroundJob) -> None:
    """Phase 13: creates the DeviceCommand that remediates one
    PolicyViolation — decoupled from the decision to remediate
    (ComplianceService._maybe_trigger_remediation, synchronous, within an
    evaluate_device call) the same way COMMAND_DISPATCH decouples
    provider dispatch from command creation. Idempotent the same way
    process_dispatch_job is: if the violation has moved on from
    REMEDIATION_PENDING (an admin resolved/disabled it, or a duplicate
    trigger — though dedup_key should already prevent that), this is a
    no-op, not a re-attempt."""
    violation_repo = PolicyViolationRepository(session)
    violation_id = uuid.UUID(str(job.payload["violation_id"]))
    violation = await violation_repo.get_for_organization(job.organization_id, violation_id)
    if violation is None or violation.status != ViolationStatus.REMEDIATION_PENDING:
        return

    command_type = DeviceCommandType(str(job.payload["command_type"]))
    command_service = _build_command_service(session)
    audit_repo = AuditRepository(session)
    try:
        command = await command_service.create_command(
            job.organization_id,
            violation.device_id,
            command_type=command_type,
            payload=None,
            idempotency_key=f"remediation:{violation.id}:{violation.remediation_attempts}",
            actor_user_id=None,
        )
    except AppError as exc:
        # A synchronous creation failure (DEVICE_NOT_ENROLLED,
        # COMMAND_TYPE_NOT_SUPPORTED, ...) — this specific attempt could
        # not even be created. Reopen to OPEN rather than deciding
        # REMEDIATION_FAILED here: remediation_attempts was already
        # incremented when this job was enqueued, and
        # ComplianceService._maybe_trigger_remediation already owns the
        # "attempts >= max_attempts -> REMEDIATION_FAILED" decision — the
        # device's next evaluation re-runs that same check (against the
        # CURRENT policy config, which may have changed) rather than this
        # job duplicating that logic.
        await violation_repo.update(violation, status=ViolationStatus.OPEN)
        logger.warning(
            "compliance_remediation_command_creation_failed",
            violation_id=str(violation.id),
            error_code=exc.code,
        )
        return

    await violation_repo.update(
        violation,
        status=ViolationStatus.REMEDIATING,
        last_remediation_command_id=command.id,
        last_remediation_at=datetime.now(tz=UTC),
    )
    await audit_repo.log_event(
        event_type=AuditEventType.COMPLIANCE_REMEDIATION_TRIGGERED,
        organization_id=job.organization_id,
        metadata={
            "violation_id": str(violation.id),
            "device_id": str(violation.device_id),
            "command_id": str(command.id),
            "stage": "command_created",
        },
    )


async def _process_command_dispatch(session: AsyncSession, job: BackgroundJob) -> None:
    command_id = uuid.UUID(str(job.payload["device_command_id"]))
    await _build_command_service(session).process_dispatch_job(job.organization_id, command_id)


async def _process_policy_reevaluation_fanout(
    session: AsyncSession, job: BackgroundJob, job_repo: BackgroundJobRepository
) -> None:
    scope_type = PolicyScopeType(job.payload["scope_type"])
    scope_id = uuid.UUID(str(job.payload["scope_id"]))
    cursor_offset = int(job.payload.get("cursor_offset", 0))
    page_count = int(job.payload.get("page_count", 0))

    device_repo = DeviceRepository(session)
    device_group_repo = DeviceGroupRepository(session)

    devices: Sequence[Device]
    if scope_type == PolicyScopeType.DEVICE_GROUP:
        devices = await device_group_repo.list_devices_for_group(
            scope_id, offset=cursor_offset, limit=_FANOUT_PAGE_SIZE
        )
    elif scope_type == PolicyScopeType.USER:
        devices = await device_repo.list_for_organization(
            job.organization_id,
            assigned_user_id=scope_id,
            offset=cursor_offset,
            limit=_FANOUT_PAGE_SIZE,
        )
    else:  # ORGANIZATION — scope_id is the organization itself.
        devices = await device_repo.list_for_organization(
            job.organization_id, offset=cursor_offset, limit=_FANOUT_PAGE_SIZE
        )

    compliance_service = _build_compliance_service(session)
    for device in devices:
        await compliance_service.evaluate_device(job.organization_id, device.id, actor_user_id=None)

    if len(devices) == _FANOUT_PAGE_SIZE and page_count + 1 < _FANOUT_MAX_PAGES:
        await job_repo.create(
            job.organization_id,
            job_type=JobType.POLICY_REEVALUATION_FANOUT,
            status=JobStatus.PENDING,
            payload={
                "scope_type": scope_type.value,
                "scope_id": str(scope_id),
                "cursor_offset": cursor_offset + _FANOUT_PAGE_SIZE,
                "page_count": page_count + 1,
            },
            dedup_key=None,
            attempts=0,
            scheduled_at=datetime.now(tz=UTC),
        )


async def _process_job(
    session: AsyncSession, job: BackgroundJob, job_repo: BackgroundJobRepository
) -> None:
    if job.job_type == JobType.COMPLIANCE_EVALUATION:
        await _process_compliance_evaluation(session, job)
    elif job.job_type == JobType.POLICY_REEVALUATION_FANOUT:
        await _process_policy_reevaluation_fanout(session, job, job_repo)
    elif job.job_type == JobType.COMMAND_DISPATCH:
        await _process_command_dispatch(session, job)
    elif job.job_type == JobType.COMPLIANCE_REMEDIATION:
        await _process_compliance_remediation(session, job)
    else:
        raise AssertionError(f"Unhandled JobType: {job.job_type!r}")  # pragma: no cover


async def run_worker_once(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    """Runs one worker cycle: the reconciliation sweep, then at most one
    background job. Returns True if a job was claimed (whether it went on
    to succeed or fail), False if the queue was empty — callers use this to
    decide whether to poll again immediately or sleep."""
    async with session_factory() as session:
        command_service = _build_command_service(session)
        reconciled = await command_service.reconcile_expired_sent_commands()
        await session.commit()
        if reconciled:
            logger.info("worker_reconciled_expired_commands", count=reconciled)

    async with session_factory() as session:
        job_repo = BackgroundJobRepository(session)
        job = await job_repo.claim_next()
        if job is None:
            await session.commit()
            return False
        try:
            await _process_job(session, job, job_repo)
            await job_repo.mark_completed(job)
            logger.info("worker_job_completed", job_id=str(job.id), job_type=job.job_type.value)
        except Exception as exc:  # noqa: BLE001 - a job failure must never crash the loop
            retry = is_transient(exc)
            await job_repo.mark_failed(job, error=str(exc), retry=retry)
            logger.warning(
                "worker_job_failed",
                job_id=str(job.id),
                job_type=job.job_type.value,
                retry=retry,
                error=str(exc),
            )
        await session.commit()
    return True


async def run_worker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    poll_interval_seconds: float = 2.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """The real, indefinitely-running loop — used by scripts/worker.py.
    Tests call `run_worker_once` directly instead of this."""
    while stop_event is None or not stop_event.is_set():
        claimed = await run_worker_once(session_factory)
        if not claimed:
            await asyncio.sleep(poll_interval_seconds)
