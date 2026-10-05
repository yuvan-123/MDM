"""Remote device command creation and dispatch (Phase 6).

All commands dispatch through MockManagementProvider (platform_provider_mode
defaults to "mock") — no real network call is ever made by this suite.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus
from app.devices.models import Device
from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login, dispatch_pending_command


async def _enroll_device(
    client: AsyncClient, csrf: str, *, display_name: str = "Command Test Device"
) -> tuple[str, str]:
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
            "display_name": display_name,
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    assert reg_res.status_code == 201, reg_res.text
    return reg_res.json()["device_id"], reg_res.json()["device_secret"]


@pytest.mark.asyncio
async def test_lock_command_completes_via_mock_provider(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """Phase 8: creation only enqueues dispatch — the POST response is
    PENDING, not the provider's outcome. dispatch_pending_command drives
    the same processing app/workers/loop.py's worker would, synchronously,
    so the test can assert on the actual dispatch result."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock",
        headers={"X-CSRF-Token": csrf},
        json={"message": "Please return to IT"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["command_type"] == "LOCK"
    assert body["status"] == "PENDING"
    assert body["payload"] == {"message": "Please return to IT"}

    dispatched = await dispatch_pending_command(db_session, organization_a.id, body["id"])
    assert dispatched.status == DeviceCommandStatus.COMPLETED
    assert dispatched.provider_name == "mock"
    assert dispatched.provider_command_id is not None


@pytest.mark.asyncio
async def test_reboot_command_completes(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/reboot",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "PENDING"
    assert res.json()["command_type"] == "REBOOT"

    dispatched = await dispatch_pending_command(db_session, organization_a.id, res.json()["id"])
    assert dispatched.status == DeviceCommandStatus.COMPLETED


@pytest.mark.asyncio
async def test_policy_sync_command_completes(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/policy-sync",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "PENDING"
    assert res.json()["command_type"] == "POLICY_SYNC"

    dispatched = await dispatch_pending_command(db_session, organization_a.id, res.json()["id"])
    assert dispatched.status == DeviceCommandStatus.COMPLETED


@pytest.mark.asyncio
async def test_refresh_info_command_updates_device_fields(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/refresh",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "PENDING"
    assert res.json()["command_type"] == "REFRESH_INFO"

    dispatched = await dispatch_pending_command(db_session, organization_a.id, res.json()["id"])
    assert dispatched.status == DeviceCommandStatus.COMPLETED

    device_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert device_res.status_code == 200
    assert device_res.json()["os_version"] == "Simulated OS 1.0"
    assert device_res.json()["client_version"] == "mock-agent-1.0"


@pytest.mark.asyncio
async def test_wipe_command_requires_confirm_and_idempotency_key(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    missing_confirm = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": "wipe-key-1"},
    )
    assert missing_confirm.status_code == 422

    missing_key = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True},
    )
    assert missing_key.status_code == 422

    ok = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True, "idempotency_key": "wipe-key-1"},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "PENDING"
    assert ok.json()["command_type"] == "WIPE"

    dispatched = await dispatch_pending_command(db_session, organization_a.id, ok.json()["id"])
    assert dispatched.status == DeviceCommandStatus.COMPLETED


@pytest.mark.asyncio
async def test_command_for_unknown_device_is_404(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/devices/00000000-0000-0000-0000-000000000000/commands/lock",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_command_for_unenrolled_device_is_409(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    """device_in_org_a is a plain Phase-4-style device (enrollment_status
    NOT_ENROLLED) — no enrollment/heartbeat credential exists for it."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_in_org_a.id}/commands/lock",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "DEVICE_NOT_ENROLLED"


@pytest.mark.asyncio
async def test_repeated_idempotency_key_returns_same_command(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    first = await client.post(
        f"/api/v1/devices/{device_id}/commands/reboot",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": "reboot-key-1"},
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/v1/devices/{device_id}/commands/reboot",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": "reboot-key-1"},
    )
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_list_and_get_command(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    created = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": csrf}, json={}
    )
    command_id = created.json()["id"]

    list_res = await client.get(
        f"/api/v1/devices/{device_id}/commands", headers={"X-CSRF-Token": csrf}
    )
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1
    assert list_res.json()["items"][0]["id"] == command_id

    get_res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{command_id}", headers={"X-CSRF-Token": csrf}
    )
    assert get_res.status_code == 200
    assert get_res.json()["id"] == command_id
