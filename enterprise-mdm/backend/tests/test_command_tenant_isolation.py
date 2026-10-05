"""Mandatory cross-tenant isolation tests for device commands (Phase 6).

Commands are nested under the flat, header-scoped /devices resource (same
model as Phase 4 devices, Phase 5 enrollment) — a cross-tenant device_id or
command_id in the URL is 404 by construction (every DeviceCommandRepository
method filters on the caller's own resolved organization_id), while a
forged X-Organization-ID header naming an org the caller isn't a member of
is 403 ORGANIZATION_ACCESS_DENIED at context-resolution time, before any
command query even runs — mirrors test_device_tenant_isolation.py and
test_phase5_tenant_isolation.py.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login
from tests.test_commands import _enroll_device


@pytest.mark.asyncio
async def test_org_a_cannot_read_org_b_command(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    device_id, _ = await _enroll_device(client, b_csrf, display_name="Org B Command Device")
    created = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": b_csrf}, json={}
    )
    assert created.status_code == 201, created.text
    command_id = created.json()["id"]

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{command_id}", headers={"X-CSRF-Token": a_csrf}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] in ("DEVICE_NOT_FOUND", "COMMAND_NOT_FOUND")


@pytest.mark.asyncio
async def test_org_a_cannot_issue_command_to_org_b_device(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    device_id, _ = await _enroll_device(client, b_csrf, display_name="Org B Target Device")

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": a_csrf}, json={}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_org_a_cannot_cancel_org_b_command(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    device_id, _ = await _enroll_device(client, b_csrf, display_name="Org B Cancel Device")
    created = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": b_csrf}, json={}
    )
    command_id = created.json()["id"]

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/{command_id}/cancel",
        headers={"X-CSRF-Token": a_csrf},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_forged_organization_header_rejected_for_commands(
    client: AsyncClient, org_a_admin_user: User, organization_b: Organization
) -> None:
    """org_a_admin_user belongs only to organization_a. Presenting
    X-Organization-ID: organization_b must be rejected — never trusted."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        "/api/v1/devices/00000000-0000-0000-0000-000000000000/commands",
        headers={"X-CSRF-Token": csrf, "X-Organization-ID": str(organization_b.id)},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"
