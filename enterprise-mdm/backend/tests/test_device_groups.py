"""Device group CRUD, membership, and tenant isolation (Phase 5, STEP 11)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.devices.models import Device
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_create_and_list_device_group(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/device-groups",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Sales Team", "description": "All sales devices"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["name"] == "Sales Team"
    assert res.json()["device_count"] == 0

    list_res = await client.get("/api/v1/device-groups", headers={"X-CSRF-Token": csrf})
    assert list_res.status_code == 200
    assert list_res.json()["total"] == 1


@pytest.mark.asyncio
async def test_duplicate_group_name_rejected(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": csrf}, json={"name": "Engineering"}
    )
    res = await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": csrf}, json={"name": "Engineering"}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "DEVICE_GROUP_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_update_and_delete_group(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": csrf}, json={"name": "Marketing"}
    )
    group_id = created.json()["id"]

    update_res = await client.patch(
        f"/api/v1/device-groups/{group_id}",
        headers={"X-CSRF-Token": csrf},
        json={"description": "Updated description"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["description"] == "Updated description"

    delete_res = await client.delete(
        f"/api/v1/device-groups/{group_id}", headers={"X-CSRF-Token": csrf}
    )
    assert delete_res.status_code == 204

    get_res = await client.get(
        f"/api/v1/device-groups/{group_id}", headers={"X-CSRF-Token": csrf}
    )
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_add_and_remove_device(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": csrf}, json={"name": "IT Group"}
    )
    group_id = created.json()["id"]

    add_res = await client.post(
        f"/api/v1/device-groups/{group_id}/devices/{device_in_org_a.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert add_res.status_code == 204

    devices_res = await client.get(
        f"/api/v1/device-groups/{group_id}/devices", headers={"X-CSRF-Token": csrf}
    )
    assert devices_res.status_code == 200
    ids = {d["id"] for d in devices_res.json()}
    assert str(device_in_org_a.id) in ids

    group_res = await client.get(
        f"/api/v1/device-groups/{group_id}", headers={"X-CSRF-Token": csrf}
    )
    assert group_res.json()["device_count"] == 1

    remove_res = await client.delete(
        f"/api/v1/device-groups/{group_id}/devices/{device_in_org_a.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert remove_res.status_code == 204

    after_res = await client.get(
        f"/api/v1/device-groups/{group_id}", headers={"X-CSRF-Token": csrf}
    )
    assert after_res.json()["device_count"] == 0


@pytest.mark.asyncio
async def test_help_desk_cannot_create_group(
    client: AsyncClient, org_a_member_user: User
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": csrf}, json={"name": "Unauthorized"}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_group_access_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
) -> None:
    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": a_csrf}, json={"name": "Org A Only"}
    )
    group_id = created.json()["id"]

    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    res = await client.get(
        f"/api/v1/device-groups/{group_id}", headers={"X-CSRF-Token": b_csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_cannot_add_cross_tenant_device_to_group(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/device-groups", headers={"X-CSRF-Token": csrf}, json={"name": "No Cross Tenant"}
    )
    group_id = created.json()["id"]

    res = await client.post(
        f"/api/v1/device-groups/{group_id}/devices/{device_in_org_b.id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"
