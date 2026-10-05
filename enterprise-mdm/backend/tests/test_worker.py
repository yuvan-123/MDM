"""Phase 7 worker loop tests — run_worker_once's reconciliation sweep, its
COMPLIANCE_EVALUATION job processing, and its POLICY_REEVALUATION_FANOUT
pagination/re-enqueue behavior.

`run_worker_once` opens fresh sessions via its `session_factory` parameter
(so a real deployment's worker gets one transaction per phase). The shared
`db_session` fixture that every other test in this suite relies on is a
single already-open session bound to an in-memory SQLite database — a
second, genuinely new connection from the same in-memory engine would see
an empty database, not the fixtures this test just wrote. `_SharedSessionFactory`
below wraps that single shared session behind the `async with
session_factory() as session` shape `run_worker_once` expects, exactly like
conftest.py's own `client` fixture wraps it behind `get_db_session` for
HTTP requests — this is the established pattern for exercising
session-factory-shaped code against the shared-session test harness.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import TracebackType

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommand, DeviceCommandStatus, DeviceCommandType
from app.commands.service import CommandService
from app.compliance.models import ComplianceStatus
from app.devices.enums import DevicePlatform, DeviceType, OwnershipType
from app.devices.models import Device
from app.jobs.models import BackgroundJob, JobStatus, JobType
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.registry import PlatformProviderRegistry
from app.policies.models import PolicyScopeType
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.compliance_repository import ComplianceRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from app.workers import loop as worker_loop
from tests.conftest import _login
from tests.test_commands import _enroll_device


class _SharedSessionCM:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class _SharedSessionFactory:
    """Test double standing in for `async_sessionmaker` — see module docstring."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SharedSessionCM:
        return _SharedSessionCM(self._session)


async def _get_job(db_session: AsyncSession, job_id: uuid.UUID) -> BackgroundJob:
    result = await db_session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
    return result.scalar_one()


async def _get_command_status(
    db_session: AsyncSession, command_id: uuid.UUID
) -> DeviceCommandStatus:
    result = await db_session.execute(
        select(DeviceCommand).where(DeviceCommand.id == command_id)
    )
    return result.scalar_one().status


@pytest.mark.asyncio
async def test_run_worker_once_returns_false_on_empty_queue(db_session: AsyncSession) -> None:
    claimed = await worker_loop.run_worker_once(_SharedSessionFactory(db_session))
    assert claimed is False


@pytest.mark.asyncio
async def test_run_worker_once_reconciles_expired_sent_commands(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    command_service = CommandService(
        command_repo=DeviceCommandRepository(db_session),
        device_repo=DeviceRepository(db_session),
        provider_registry=PlatformProviderRegistry(),
        audit_repo=AuditRepository(db_session),
        job_service=JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db_session),
    )
    command = await command_service.create_command(
        organization_a.id,
        uuid.UUID(device_id),
        command_type=DeviceCommandType.REBOOT,
        payload={"simulate_sent": True},
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    assert command.status == DeviceCommandStatus.PENDING
    command = await command_service.process_dispatch_job(organization_a.id, command.id)
    assert command.status == DeviceCommandStatus.SENT

    command_repo = DeviceCommandRepository(db_session)
    await command_repo.update_for_organization(
        command, expires_at=datetime.now(tz=UTC) - timedelta(hours=1)
    )

    await worker_loop.run_worker_once(_SharedSessionFactory(db_session))

    status = await _get_command_status(db_session, command.id)
    assert status == DeviceCommandStatus.EXPIRED


@pytest.mark.asyncio
async def test_run_worker_once_processes_compliance_evaluation_job(
    db_session: AsyncSession, organization_a: Organization, device_in_org_a: Device
) -> None:
    job_service = JobService(BackgroundJobRepository(db_session), AuditRepository(db_session))
    job = await job_service.enqueue_compliance_evaluation(organization_a.id, device_in_org_a.id)
    assert job is not None

    claimed = await worker_loop.run_worker_once(_SharedSessionFactory(db_session))
    assert claimed is True

    processed = await _get_job(db_session, job.id)
    assert processed.status == JobStatus.COMPLETED

    compliance = await ComplianceRepository(db_session).get_for_device(
        organization_a.id, device_in_org_a.id
    )
    assert compliance is not None
    # No policy assigned to this device/org — evaluates to UNKNOWN, not an error.
    assert compliance.status == ComplianceStatus.UNKNOWN


@pytest.mark.asyncio
async def test_policy_reevaluation_fanout_pages_through_every_device(
    db_session: AsyncSession,
    organization_a: Organization,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses a page size of 2 (monkeypatched) against 5 devices so the fanout
    chain must re-enqueue itself twice before the final, partial page stops
    the chain — proves both the pagination and the re-enqueue-with-advancing-
    cursor behavior without needing hundreds of real devices."""
    monkeypatch.setattr(worker_loop, "_FANOUT_PAGE_SIZE", 2)

    device_repo = DeviceRepository(db_session)
    group_repo = DeviceGroupRepository(db_session)
    group = await group_repo.create(organization_a.id, name="Fanout Test Group")

    devices = []
    for i in range(5):
        device = await device_repo.create_for_organization(
            organization_a.id,
            display_name=f"Fanout Device {i}",
            platform=DevicePlatform.ANDROID,
            device_type=DeviceType.PHONE,
            ownership_type=OwnershipType.CORPORATE,
        )
        await group_repo.add_member(group.id, device.id)
        devices.append(device)
    await db_session.commit()

    job_service = JobService(BackgroundJobRepository(db_session), AuditRepository(db_session))
    job = await job_service.enqueue_policy_reevaluation(
        organization_a.id, scope_type=PolicyScopeType.DEVICE_GROUP, scope_id=group.id
    )
    assert job is not None
    assert job.job_type == JobType.POLICY_REEVALUATION_FANOUT

    factory = _SharedSessionFactory(db_session)
    # ceil(5/2) = 3 fanout job cycles expected; give it a couple of extra
    # cycles of headroom and assert no job is left PENDING/CLAIMED afterward
    # rather than asserting an exact cycle count.
    for _ in range(6):
        await worker_loop.run_worker_once(factory)

    result = await db_session.execute(
        select(BackgroundJob).where(
            BackgroundJob.organization_id == organization_a.id,
            BackgroundJob.job_type == JobType.POLICY_REEVALUATION_FANOUT,
        )
    )
    fanout_jobs = result.scalars().all()
    assert len(fanout_jobs) == 3  # offsets 0, 2, 4
    assert all(j.status == JobStatus.COMPLETED for j in fanout_jobs)
    offsets = sorted(j.payload["cursor_offset"] for j in fanout_jobs)
    assert offsets == [0, 2, 4]

    compliance_repo = ComplianceRepository(db_session)
    for device in devices:
        compliance = await compliance_repo.get_for_device(organization_a.id, device.id)
        assert compliance is not None
        assert compliance.status == ComplianceStatus.UNKNOWN


@pytest.mark.asyncio
async def test_run_worker_once_processes_command_dispatch_job(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """Phase 8 end-to-end: creating a command via the HTTP API only enqueues
    a COMMAND_DISPATCH job (the response is PENDING); the worker claims and
    processes it exactly like any other job type."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": csrf}, json={}
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "PENDING"
    command_id = uuid.UUID(res.json()["id"])

    result = await db_session.execute(
        select(BackgroundJob).where(
            BackgroundJob.organization_id == organization_a.id,
            BackgroundJob.job_type == JobType.COMMAND_DISPATCH,
        )
    )
    jobs = result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.PENDING
    assert jobs[0].payload == {"device_command_id": str(command_id)}

    claimed = await worker_loop.run_worker_once(_SharedSessionFactory(db_session))
    assert claimed is True

    final = await db_session.execute(
        select(DeviceCommand).where(DeviceCommand.id == command_id)
    )
    command = final.scalar_one()
    assert command.status == DeviceCommandStatus.COMPLETED
    assert command.provider_command_id is not None


@pytest.mark.asyncio
async def test_command_dispatch_job_retry_does_not_redispatch(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """Simulates a retried/duplicate COMMAND_DISPATCH job claim for a
    command that has already been dispatched — process_dispatch_job must be
    a no-op (never call the provider a second time for the same command),
    the core WIPE-safety concern behind async dispatch (Phase 8 §15)."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": csrf}, json={}
    )
    command_id = res.json()["id"]

    from tests.conftest import dispatch_pending_command

    first_result = await dispatch_pending_command(db_session, organization_a.id, command_id)
    assert first_result.status == DeviceCommandStatus.COMPLETED
    first_provider_command_id = first_result.provider_command_id

    # A second dispatch attempt against the same (already-terminal) command
    # — e.g. a worker reclaiming a job whose lease looked expired but whose
    # first attempt actually succeeded and committed.
    second_result = await dispatch_pending_command(db_session, organization_a.id, command_id)
    assert second_result.status == DeviceCommandStatus.COMPLETED
    assert second_result.provider_command_id == first_provider_command_id


@pytest.mark.asyncio
async def test_claim_next_recovers_a_lease_expired_claimed_job(
    db_session: AsyncSession, organization_a: Organization, device_in_org_a: Device
) -> None:
    """A worker that crashes mid-job leaves it stuck CLAIMED — claim_next
    must reclaim it once its lease has expired rather than leaving it
    stuck forever (Phase 8 §20)."""
    from app.repositories.background_job_repository import JOB_LEASE_TIMEOUT

    job_repo = BackgroundJobRepository(db_session)
    job = await JobService(job_repo, AuditRepository(db_session)).enqueue_compliance_evaluation(
        organization_a.id, device_in_org_a.id
    )
    assert job is not None

    claimed = await job_repo.claim_next()
    assert claimed is not None
    assert claimed.attempts == 1

    # Simulate a crashed worker: the lease is long expired, nothing ever
    # marked the job completed/failed.
    claimed.claimed_at = datetime.now(tz=UTC) - JOB_LEASE_TIMEOUT - timedelta(minutes=1)
    await db_session.flush()

    reclaimed = await job_repo.claim_next()
    assert reclaimed is not None
    assert reclaimed.id == claimed.id
    assert reclaimed.attempts == 2  # a genuinely new attempt, not the same one
