"""Mandatory cross-tenant isolation tests for devices (Phase 4, STEP 31-33).

Devices are a flat, header-scoped resource (X-Organization-ID), unlike
organizations' path-scoped `{organization_id}` — so the IDOR story is
different from test_tenant_isolation.py: a cross-tenant device_id in the
URL is 404 by construction (DeviceRepository.get_for_organization filters
on the caller's *own* resolved organization_id — a device belonging to
another org simply isn't found), while a forged X-Organization-ID header
naming an org the caller isn't a member of is 403
ORGANIZATION_ACCESS_DENIED at context-resolution time, before any device
query even runs.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.devices.models import Device
from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_cross_tenant_get_returns_404_not_leak(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_b: Device,
) -> None:
    """STEP 32 IDOR: org A's admin knows org B's device ID but must get a
    plain 404, never a 403 that would confirm the device exists."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/devices/{device_in_org_b.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_cross_tenant_patch_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.patch(
        f"/api/v1/devices/{device_in_org_b.id}",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Hostile Rename"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_cross_tenant_delete_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.delete(
        f"/api/v1/devices/{device_in_org_b.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_assign_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_second_admin_user: User,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_in_org_b.id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_a_second_admin_user.id)},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_block_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_in_org_b.id}/block", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_cross_tenant_retire_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_in_org_b.id}/retire", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_forged_organization_header_rejected(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_b: Organization,
) -> None:
    """STEP 31: org_a_admin_user belongs only to organization_a. Presenting
    X-Organization-ID: organization_b must be rejected — never trusted."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf, "X-Organization-ID": str(organization_b.id)},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_devices_list_is_scoped_to_caller_organization(
    client: AsyncClient,
    org_a_admin_user: User,
    device_in_org_a: Device,
    device_in_org_b: Device,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get("/api/v1/devices", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    ids = {item["id"] for item in res.json()["items"]}
    assert str(device_in_org_a.id) in ids
    assert str(device_in_org_b.id) not in ids


@pytest.mark.asyncio
async def test_disabled_organization_denies_device_access(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    super_admin_user: User,
) -> None:
    """STEP 35 case #27: a disabled organization's members lose device access."""
    super_csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    disable_res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}/status",
        headers={"X-CSRF-Token": super_csrf},
        json={"status": "DISABLED"},
    )
    assert disable_res.status_code == 200

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get("/api/v1/devices", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_SUSPENDED"
