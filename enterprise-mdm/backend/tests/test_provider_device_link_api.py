"""Provider-device-link admin API (Phase 10) — CRUD, RBAC, tenant
isolation, uniqueness, and safe unlinking. See
app/provider_events/link_router.py and app/provider_events/link_service.py."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.devices.enums import DevicePlatform, DeviceType, OwnershipType
from app.devices.models import Device
from app.organizations.models import Organization
from app.repositories.device_repository import DeviceRepository
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_organization_admin_can_create_list_get_link(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    device_in_org_a: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    created = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_a.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/abc123",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["device_id"] == str(device_in_org_a.id)
    assert body["provider_name"] == "android"
    assert body["link_source"] == "MANUAL"
    assert body["apple_push_token"] is None
    link_id = body["id"]

    list_res = await client.get(
        "/api/v1/provider-device-links", headers={"X-CSRF-Token": csrf}
    )
    assert list_res.status_code == 200
    assert any(item["id"] == link_id for item in list_res.json()["items"])

    get_res = await client.get(
        f"/api/v1/provider-device-links/{link_id}", headers={"X-CSRF-Token": csrf}
    )
    assert get_res.status_code == 200
    assert get_res.json()["id"] == link_id


@pytest.mark.asyncio
async def test_create_link_rejects_device_from_a_different_organization(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    device_in_org_b: Device,
) -> None:
    """The core tenant-isolation requirement: Organization A must never be
    able to create a link pointing at Organization B's device, even though
    the device_id is a syntactically valid UUID that genuinely exists."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_b.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/cross-tenant",
        },
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_link_rejects_unrecognized_provider(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_a.id),
            "provider_name": "not-a-real-provider",
            "provider_device_id": "whatever",
        },
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "PROVIDER_DEVICE_LINK_INVALID_PROVIDER"


@pytest.mark.asyncio
async def test_create_link_rejects_duplicate_mapping(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """Structural uniqueness: the same (org, provider, provider_device_id)
    can never map to two different devices."""
    device_repo = DeviceRepository(db_session)
    device_1 = await device_repo.create_for_organization(
        organization_a.id,
        display_name="Device One",
        platform=DevicePlatform.ANDROID,
        device_type=DeviceType.PHONE,
        ownership_type=OwnershipType.CORPORATE,
    )
    device_2 = await device_repo.create_for_organization(
        organization_a.id,
        display_name="Device Two",
        platform=DevicePlatform.ANDROID,
        device_type=DeviceType.PHONE,
        ownership_type=OwnershipType.CORPORATE,
    )
    await db_session.commit()

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    first = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_1.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/dup-1",
        },
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_2.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/dup-1",
        },
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PROVIDER_DEVICE_LINK_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_update_link_can_change_provider_device_id(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_a.id),
            "provider_name": "apple",
            "provider_device_id": "OLD-UDID",
        },
    )
    link_id = created.json()["id"]

    updated = await client.patch(
        f"/api/v1/provider-device-links/{link_id}",
        headers={"X-CSRF-Token": csrf},
        json={"provider_device_id": "NEW-UDID", "apple_push_token": "abc123"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["provider_device_id"] == "NEW-UDID"
    assert updated.json()["apple_push_token"] == "abc123"


@pytest.mark.asyncio
async def test_delete_link_does_not_delete_the_device(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_a.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/unlink-me",
        },
    )
    link_id = created.json()["id"]

    deleted = await client.delete(
        f"/api/v1/provider-device-links/{link_id}", headers={"X-CSRF-Token": csrf}
    )
    assert deleted.status_code == 204

    gone = await client.get(
        f"/api/v1/provider-device-links/{link_id}", headers={"X-CSRF-Token": csrf}
    )
    assert gone.status_code == 404

    # The internal device itself is untouched — still a full MDM object.
    still_there = await client.get(
        f"/api/v1/devices/{device_in_org_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert still_there.status_code == 200


@pytest.mark.asyncio
async def test_org_a_cannot_see_or_modify_org_b_links(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    organization_b: Organization,
    device_in_org_b: Device,
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    created = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": b_csrf},
        json={
            "device_id": str(device_in_org_b.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/org-b-device",
        },
    )
    link_id = created.json()["id"]

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    get_res = await client.get(
        f"/api/v1/provider-device-links/{link_id}", headers={"X-CSRF-Token": a_csrf}
    )
    assert get_res.status_code == 404

    patch_res = await client.patch(
        f"/api/v1/provider-device-links/{link_id}",
        headers={"X-CSRF-Token": a_csrf},
        json={"provider_device_id": "hijacked"},
    )
    assert patch_res.status_code == 404

    delete_res = await client.delete(
        f"/api/v1/provider-device-links/{link_id}", headers={"X-CSRF-Token": a_csrf}
    )
    assert delete_res.status_code == 404


@pytest.mark.asyncio
async def test_help_desk_cannot_manage_provider_device_links(
    client: AsyncClient, org_a_member_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_a.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/nope",
        },
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_auditor_can_read_but_not_create_links(
    client: AsyncClient, org_a_auditor_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")
    list_res = await client.get(
        "/api/v1/provider-device-links", headers={"X-CSRF-Token": csrf}
    )
    assert list_res.status_code == 200

    create_res = await client.post(
        "/api/v1/provider-device-links",
        headers={"X-CSRF-Token": csrf},
        json={
            "device_id": str(device_in_org_a.id),
            "provider_name": "android",
            "provider_device_id": "enterprises/e/devices/auditor-attempt",
        },
    )
    assert create_res.status_code == 403
