"""Device heartbeat / check-in (Phase 5, STEP 4).

Heartbeat is authenticated via X-Device-Id/X-Device-Secret headers, NOT an
admin JWT — see app/enrollment/dependencies.py::get_enrolled_device.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.devices.models import Device
from app.jobs.models import BackgroundJob, JobStatus, JobType
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_repository import DeviceRepository
from app.users.models import User
from tests.conftest import _login


async def _enroll_device(client: AsyncClient, csrf: str) -> tuple[str, str]:
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Heartbeat Test Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    assert reg_res.status_code == 201, reg_res.text
    return reg_res.json()["device_id"], reg_res.json()["device_secret"]


@pytest.mark.asyncio
async def test_valid_heartbeat_updates_last_seen(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={"client_version": "1.2.3", "os_version": "14.1"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["device_id"] == device_id
    assert body["connectivity_state"] == "ONLINE"
    assert body["last_seen_at"] is not None

    device_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert device_res.json()["client_version"] == "1.2.3"
    assert device_res.json()["os_version"] == "14.1"
    assert device_res.json()["connectivity_state"] == "ONLINE"


@pytest.mark.asyncio
async def test_heartbeat_with_wrong_secret_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": "totally-wrong-secret"},
        json={},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "DEVICE_CREDENTIAL_INVALID"


@pytest.mark.asyncio
async def test_heartbeat_for_unknown_device_rejected(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/devices/00000000-0000-0000-0000-000000000000/heartbeat",
        headers={
            "X-Device-Id": "00000000-0000-0000-0000-000000000000",
            "X-Device-Secret": "irrelevant",
        },
        json={},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "DEVICE_CREDENTIAL_INVALID"


@pytest.mark.asyncio
async def test_heartbeat_device_id_header_must_match_path(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    """A device's own valid credential cannot be used to heartbeat as a
    *different* device_id in the URL path."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_in_org_a.id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "DEVICE_CREDENTIAL_INVALID"


@pytest.mark.asyncio
async def test_pending_enrollment_device_cannot_heartbeat(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    """A device still awaiting approval (enrollment_status=PENDING) has no
    working heartbeat credential check pass — only ENROLLED devices may
    check in."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},  # require_approval defaults True
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Pending Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    device_id = reg_res.json()["device_id"]
    device_secret = reg_res.json()["device_secret"]

    res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_connectivity_state_stale_and_offline(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_a: Device,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    never = await client.get(
        f"/api/v1/devices/{device_in_org_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert never.json()["connectivity_state"] == "NEVER_CHECKED_IN"

    device_repo = DeviceRepository(db_session)
    device = await device_repo.get_for_organization(
        device_in_org_a.organization_id, device_in_org_a.id
    )
    assert device is not None
    await device_repo.update_for_organization(
        device, last_seen_at=datetime.now(tz=UTC) - timedelta(hours=2)
    )
    await db_session.commit()

    stale = await client.get(
        f"/api/v1/devices/{device_in_org_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert stale.json()["connectivity_state"] == "STALE"

    await device_repo.update_for_organization(
        device, last_seen_at=datetime.now(tz=UTC) - timedelta(hours=48)
    )
    await db_session.commit()

    offline = await client.get(
        f"/api/v1/devices/{device_in_org_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert offline.json()["connectivity_state"] == "OFFLINE"


@pytest.mark.asyncio
async def test_heartbeat_enqueues_one_compliance_job_despite_repeated_checkins(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    """Phase 7: each heartbeat triggers a scheduled compliance evaluation,
    but a burst of check-ins from the same device (a client retrying, or
    just its normal polling cadence) must collapse into one queued job
    instead of flooding the worker queue — see
    app/jobs/service.py::JobService.enqueue_compliance_evaluation's
    dedup_key."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf)
    headers = {"X-Device-Id": device_id, "X-Device-Secret": device_secret}

    for _ in range(3):
        res = await client.post(
            f"/api/v1/devices/{device_id}/heartbeat", headers=headers, json={}
        )
        assert res.status_code == 200, res.text

    device = await DeviceRepository(db_session).get_by_id_unscoped(uuid.UUID(device_id))
    assert device is not None

    job_repo = BackgroundJobRepository(db_session)
    active = await job_repo.get_active_by_dedup_key(
        device.organization_id,
        JobType.COMPLIANCE_EVALUATION,
        f"compliance_evaluation:{device_id}",
    )
    assert active is not None
    assert active.status == JobStatus.PENDING

    result = await db_session.execute(
        select(BackgroundJob).where(
            BackgroundJob.organization_id == device.organization_id,
            BackgroundJob.job_type == JobType.COMPLIANCE_EVALUATION,
        )
    )
    assert len(result.scalars().all()) == 1, "3 heartbeats must produce exactly 1 queued job"
