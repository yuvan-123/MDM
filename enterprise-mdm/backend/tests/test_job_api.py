"""Background-job admin API (Phase 8 read, Phase 10 retry + traceability).
See app/jobs/router.py and docs/architecture/background-jobs.md."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.models import JobStatus
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.users.models import User
from tests.conftest import _login


async def _seed_job(db_session: AsyncSession, organization: Organization) -> str:
    service = JobService(BackgroundJobRepository(db_session), AuditRepository(db_session))
    job = await service.enqueue_compliance_evaluation(organization.id, uuid.uuid4())
    assert job is not None
    await db_session.commit()
    return str(job.id)


@pytest.mark.asyncio
async def test_organization_admin_can_list_and_get_jobs(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    job_id = await _seed_job(db_session, organization_a)
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    list_res = await client.get("/api/v1/jobs", headers={"X-CSRF-Token": csrf})
    assert list_res.status_code == 200, list_res.text
    assert list_res.json()["total"] >= 1
    assert any(item["id"] == job_id for item in list_res.json()["items"])

    get_res = await client.get(f"/api/v1/jobs/{job_id}", headers={"X-CSRF-Token": csrf})
    assert get_res.status_code == 200
    assert get_res.json()["id"] == job_id
    assert get_res.json()["job_type"] == "COMPLIANCE_EVALUATION"


@pytest.mark.asyncio
async def test_job_list_filters_by_status_and_job_type(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    await _seed_job(db_session, organization_a)
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    matching = await client.get(
        "/api/v1/jobs?job_type=COMPLIANCE_EVALUATION&status=PENDING",
        headers={"X-CSRF-Token": csrf},
    )
    assert matching.status_code == 200
    assert matching.json()["total"] >= 1

    empty = await client.get(
        "/api/v1/jobs?job_type=POLICY_REEVALUATION_FANOUT", headers={"X-CSRF-Token": csrf}
    )
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_org_a_cannot_see_org_b_jobs(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    job_id = await _seed_job(db_session, organization_b)

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    list_res = await client.get("/api/v1/jobs", headers={"X-CSRF-Token": a_csrf})
    assert list_res.status_code == 200
    assert all(item["id"] != job_id for item in list_res.json()["items"])

    get_res = await client.get(f"/api/v1/jobs/{job_id}", headers={"X-CSRF-Token": a_csrf})
    assert get_res.status_code == 404
    assert get_res.json()["error"]["code"] == "JOB_NOT_FOUND"

    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    own_get = await client.get(f"/api/v1/jobs/{job_id}", headers={"X-CSRF-Token": b_csrf})
    assert own_get.status_code == 200


@pytest.mark.asyncio
async def test_help_desk_cannot_read_jobs(
    client: AsyncClient, org_a_member_user: User, organization_a: Organization
) -> None:
    """HELP_DESK does not carry job.read — background jobs are operational
    infrastructure, not part of its device-facing scope."""
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.get("/api/v1/jobs", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_auditor_can_read_jobs(
    client: AsyncClient,
    org_a_auditor_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    await _seed_job(db_session, organization_a)
    csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")
    res = await client.get("/api/v1/jobs", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# Phase 10: manual retry
# ---------------------------------------------------------------------------


async def _seed_failed_job(db_session: AsyncSession, organization: Organization) -> str:
    """No public path produces a FAILED job on demand — directly
    manipulate via the repository, matching the established pattern for
    internal states the public API doesn't expose a hook for (see
    tests/test_command_state_machine.py's identical rationale)."""
    job_repo = BackgroundJobRepository(db_session)
    service = JobService(job_repo, AuditRepository(db_session))
    job = await service.enqueue_compliance_evaluation(organization.id, uuid.uuid4())
    assert job is not None
    await db_session.commit()
    job.status = JobStatus.FAILED
    job.last_error = "simulated failure for testing"
    await db_session.flush()
    await db_session.commit()
    return str(job.id)


@pytest.mark.asyncio
async def test_organization_admin_can_retry_a_failed_job(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    job_id = await _seed_failed_job(db_session, organization_a)
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    res = await client.post(f"/api/v1/jobs/{job_id}/retry", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "PENDING"
    assert res.json()["last_error"] == "simulated failure for testing"  # preserved, not reset


@pytest.mark.asyncio
async def test_cannot_retry_a_non_failed_job(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    job_id = await _seed_job(db_session, organization_a)  # PENDING, not FAILED
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    res = await client.post(f"/api/v1/jobs/{job_id}/retry", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "JOB_NOT_RETRYABLE"


@pytest.mark.asyncio
async def test_org_a_cannot_retry_org_b_job(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    job_id = await _seed_failed_job(db_session, organization_b)
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    res = await client.post(f"/api/v1/jobs/{job_id}/retry", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_auditor_cannot_retry_jobs(
    client: AsyncClient,
    org_a_auditor_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """Auditors have job.read but not job.retry — seeing a failure must
    not imply the authority to re-trigger it."""
    job_id = await _seed_failed_job(db_session, organization_a)
    csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")

    res = await client.post(f"/api/v1/jobs/{job_id}/retry", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Phase 10: command -> job traceability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_list_filters_by_command_id(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    job_repo = BackgroundJobRepository(db_session)
    service = JobService(job_repo, AuditRepository(db_session))
    command_id = uuid.uuid4()
    job = await service.enqueue_command_dispatch(organization_a.id, command_id)
    assert job is not None
    await db_session.commit()

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/jobs?command_id={command_id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["id"] == str(job.id)

    empty = await client.get(
        f"/api/v1/jobs?command_id={uuid.uuid4()}", headers={"X-CSRF-Token": csrf}
    )
    assert empty.json()["total"] == 0
