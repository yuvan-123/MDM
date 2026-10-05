"""Organization CRUD, slug generation, and status transitions (Phase 3)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_super_admin_can_create_organization(
    client: AsyncClient, super_admin_user: User
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    res = await client.post(
        "/api/v1/organizations",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Acme Corporation"},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["name"] == "Acme Corporation"
    assert data["slug"] == "acme-corporation"
    assert data["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_non_super_admin_cannot_create_organization(
    client: AsyncClient, helpdesk_user: User
) -> None:
    csrf = await _login(client, helpdesk_user.email, "HelpDeskPass123!")
    res = await client.post(
        "/api/v1/organizations",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Should Fail Inc"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_slug_uniqueness_suffixed_on_collision(
    client: AsyncClient, super_admin_user: User
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    first = await client.post(
        "/api/v1/organizations", headers={"X-CSRF-Token": csrf}, json={"name": "Beta Corp"}
    )
    second = await client.post(
        "/api/v1/organizations", headers={"X-CSRF-Token": csrf}, json={"name": "Beta Corp"}
    )
    assert first.json()["slug"] == "beta-corp"
    assert second.json()["slug"] == "beta-corp-2"


@pytest.mark.asyncio
async def test_org_admin_can_read_own_organization(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/organizations/{organization_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200
    assert res.json()["id"] == str(organization_a.id)


@pytest.mark.asyncio
async def test_org_admin_can_update_own_organization_name(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Organization A Renamed"},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Organization A Renamed"
    # Slug is immutable once created, even when the name changes.
    assert res.json()["slug"] == "organization-a"


@pytest.mark.asyncio
async def test_org_admin_cannot_suspend_own_organization(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    """Suspend/disable is platform-only — an organization cannot suspend itself."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}/status",
        headers={"X-CSRF-Token": csrf},
        json={"status": "SUSPENDED"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_suspend_organization(
    client: AsyncClient, super_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}/status",
        headers={"X-CSRF-Token": csrf},
        json={"status": "SUSPENDED"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "SUSPENDED"


@pytest.mark.asyncio
async def test_member_of_suspended_organization_denied_access(
    client: AsyncClient,
    super_admin_user: User,
    org_a_admin_user: User,
    organization_a: Organization,
) -> None:
    admin_csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    suspend_res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}/status",
        headers={"X-CSRF-Token": admin_csrf},
        json={"status": "SUSPENDED"},
    )
    assert suspend_res.status_code == 200

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/organizations/{organization_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_SUSPENDED"
