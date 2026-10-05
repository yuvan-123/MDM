"""Policy CRUD, versioning, assignment, and tenant isolation (Phase 5, STEP 6-8)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.devices.models import Device
from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login


def _policy_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Baseline Security Policy",
        "description": "Minimum security baseline",
        "configuration": {
            "device_security": {"require_encryption": True, "minimum_os_version": "14"}
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_policy_creates_version_one(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "DRAFT"
    assert body["current_version"]["version"] == 1
    assert body["current_version"]["configuration"]["device_security"]["require_encryption"] is True


@pytest.mark.asyncio
async def test_duplicate_policy_name_rejected(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    res = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "POLICY_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_update_configuration_creates_new_version_without_mutating_old(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    update_res = await client.patch(
        f"/api/v1/policies/{policy_id}",
        headers={"X-CSRF-Token": csrf},
        json={"configuration": {"device_security": {"minimum_os_version": "15"}}},
    )
    assert update_res.status_code == 200
    assert update_res.json()["current_version"]["version"] == 2
    assert (
        update_res.json()["current_version"]["configuration"]["device_security"][
            "minimum_os_version"
        ]
        == "15"
    )

    versions_res = await client.get(
        f"/api/v1/policies/{policy_id}/versions", headers={"X-CSRF-Token": csrf}
    )
    versions = versions_res.json()
    assert len(versions) == 2
    v1 = next(v for v in versions if v["version"] == 1)
    assert v1["configuration"]["device_security"]["minimum_os_version"] == "14"


@pytest.mark.asyncio
async def test_activate_policy(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    res = await client.patch(
        f"/api/v1/policies/{policy_id}", headers={"X-CSRF-Token": csrf}, json={"status": "ACTIVE"}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_cannot_archive_via_patch(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    res = await client.patch(
        f"/api/v1/policies/{policy_id}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "ARCHIVED"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_POLICY_STATUS_TRANSITION"


@pytest.mark.asyncio
async def test_delete_archives_policy(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    res = await client.delete(
        f"/api/v1/policies/{policy_id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_delete_blocked_while_assigned(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]
    await client.patch(
        f"/api/v1/policies/{policy_id}", headers={"X-CSRF-Token": csrf}, json={"status": "ACTIVE"}
    )
    await client.post(
        f"/api/v1/policies/{policy_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "ORGANIZATION", "scope_id": str(organization_a.id)},
    )

    res = await client.delete(
        f"/api/v1/policies/{policy_id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "POLICY_HAS_ASSIGNMENTS"


@pytest.mark.asyncio
async def test_assign_and_unassign_to_organization(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    assign_res = await client.post(
        f"/api/v1/policies/{policy_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "ORGANIZATION", "scope_id": str(organization_a.id)},
    )
    assert assign_res.status_code == 200
    assert assign_res.json()["scope_type"] == "ORGANIZATION"

    assignments_res = await client.get(
        f"/api/v1/policies/{policy_id}/assignments", headers={"X-CSRF-Token": csrf}
    )
    assert len(assignments_res.json()) == 1

    unassign_res = await client.post(
        f"/api/v1/policies/{policy_id}/unassign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "ORGANIZATION", "scope_id": str(organization_a.id)},
    )
    assert unassign_res.status_code == 204


@pytest.mark.asyncio
async def test_cannot_assign_to_foreign_organization_scope(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    res = await client.post(
        f"/api/v1/policies/{policy_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "ORGANIZATION", "scope_id": str(organization_b.id)},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_POLICY_SCOPE"


@pytest.mark.asyncio
async def test_cannot_assign_to_device_in_other_organization(
    client: AsyncClient, org_a_admin_user: User, device_in_org_b: Device
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    res = await client.post(
        f"/api/v1/policies/{policy_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": "DEVICE", "scope_id": str(device_in_org_b.id)},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_help_desk_cannot_create_policy(
    client: AsyncClient, org_a_member_user: User
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": csrf}, json=_policy_payload()
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_policy_access_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
) -> None:
    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/policies", headers={"X-CSRF-Token": a_csrf}, json=_policy_payload()
    )
    policy_id = created.json()["id"]

    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    res = await client.get(f"/api/v1/policies/{policy_id}", headers={"X-CSRF-Token": b_csrf})
    assert res.status_code == 404
