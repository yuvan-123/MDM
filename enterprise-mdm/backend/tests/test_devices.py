"""Device inventory CRUD, pagination, search, and filtering (Phase 4)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login


def _device_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "John's Galaxy S24",
        "manufacturer": "Samsung",
        "model": "Galaxy S24",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_device(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["display_name"] == "John's Galaxy S24"
    assert data["organization_id"] == str(organization_a.id)
    assert data["status"] == "ACTIVE"
    assert data["enrollment_status"] == "NOT_ENROLLED"
    assert data["assigned_user_id"] is None


@pytest.mark.asyncio
async def test_create_device_ignores_client_supplied_organization_id(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    """STEP 7: organization_id from the client is never trusted — there is
    no such field on CreateDeviceRequest, so any attempt to smuggle one in
    the JSON body is silently ignored by schema validation."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(organization_id=str(organization_b.id)),
    )
    assert res.status_code == 201, res.text
    assert res.json()["organization_id"] == str(organization_a.id)


@pytest.mark.asyncio
async def test_get_device(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]
    res = await client.get(f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    assert res.json()["id"] == device_id


@pytest.mark.asyncio
async def test_get_nonexistent_device_returns_404(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        "/api/v1/devices/00000000-0000-0000-0000-000000000000",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_device(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]
    res = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Renamed Device"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["display_name"] == "Renamed Device"


@pytest.mark.asyncio
async def test_update_device_cannot_jump_straight_to_blocked(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    """`status` is a real field on UpdateDeviceRequest, but the generic
    PATCH path only permits ACTIVE/INACTIVE/LOST transitions — BLOCKED
    requires the dedicated device.block endpoint (see test_device_lifecycle.py)."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]
    res = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "BLOCKED"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


@pytest.mark.asyncio
async def test_delete_device(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]
    res = await client.delete(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 204

    follow_up = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert follow_up.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_device_identifier_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    first = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(device_identifier="DEV-001"),
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(display_name="Another Device", device_identifier="DEV-001"),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DEVICE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_duplicate_serial_number_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    first = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(serial_number="SN-001"),
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(display_name="Another Device", serial_number="SN-001"),
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "DEVICE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_same_serial_number_allowed_across_organizations(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
) -> None:
    """STEP 6: uniqueness is organization-scoped, not global."""
    csrf_a = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res_a = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf_a},
        json=_device_payload(serial_number="SHARED-SN"),
    )
    assert res_a.status_code == 201

    csrf_b = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    res_b = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf_b},
        json=_device_payload(serial_number="SHARED-SN"),
    )
    assert res_b.status_code == 201


@pytest.mark.asyncio
async def test_pagination(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    for i in range(5):
        res = await client.post(
            "/api/v1/devices",
            headers={"X-CSRF-Token": csrf},
            json=_device_payload(display_name=f"Device {i}"),
        )
        assert res.status_code == 201

    page1 = await client.get(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        params={"page": 1, "page_size": 2},
    )
    assert page1.status_code == 200
    body = page1.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["total_pages"] == 3
    assert body["page"] == 1


@pytest.mark.asyncio
async def test_pagination_rejects_excessive_page_size(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        params={"page_size": 1000000},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_search_matches_display_name_and_manufacturer(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(display_name="Samsung Galaxy S24", manufacturer="Samsung"),
    )
    await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(display_name="Pixel 9", manufacturer="Google"),
    )

    res = await client.get(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, params={"search": "samsung"}
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["display_name"] == "Samsung Galaxy S24"


@pytest.mark.asyncio
async def test_filter_by_platform_and_status(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(display_name="Android Phone", platform="ANDROID"),
    )
    await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(display_name="iPad", platform="IPADOS", device_type="TABLET"),
    )

    res = await client.get(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, params={"platform": "IPADOS"}
    )
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["display_name"] == "iPad"

    res_status = await client.get(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, params={"status": "ACTIVE"}
    )
    assert res_status.json()["total"] == 2
