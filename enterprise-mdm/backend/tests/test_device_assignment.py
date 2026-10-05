"""Device assignment — same-org enforcement, membership status, and
lifecycle interaction (Phase 4, STEP 8 / STEP 33)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import MembershipStatus, Organization
from app.repositories.membership_repository import MembershipRepository
from app.users.models import User
from tests.conftest import _login


def _device_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Assignment Test Device",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_assign_and_unassign_device(
    client: AsyncClient, org_a_admin_user: User, org_a_second_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    assign_res = await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_a_second_admin_user.id)},
    )
    assert assign_res.status_code == 200, assign_res.text
    assert assign_res.json()["assigned_user_id"] == str(org_a_second_admin_user.id)
    assert assign_res.json()["assigned_user_email"] == org_a_second_admin_user.email

    unassign_res = await client.post(
        f"/api/v1/devices/{device_id}/unassign", headers={"X-CSRF-Token": csrf}
    )
    assert unassign_res.status_code == 200
    assert unassign_res.json()["assigned_user_id"] is None


@pytest.mark.asyncio
async def test_cannot_assign_device_to_user_in_different_organization(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
) -> None:
    """STEP 33: Device A (org A) -> User B (org B) must fail."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    res = await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_b_admin_user.id)},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "USER_NOT_IN_ORGANIZATION"


@pytest.mark.asyncio
async def test_cannot_assign_device_to_suspended_member(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_second_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """STEP 35 case #25: inactive (SUSPENDED) member assignment must fail."""
    membership_repo = MembershipRepository(db_session)
    membership = await membership_repo.get_by_user_and_org(
        org_a_second_admin_user.id, organization_a.id
    )
    assert membership is not None
    await membership_repo.update_status(
        organization_a.id, membership.id, MembershipStatus.SUSPENDED
    )
    await db_session.commit()

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    res = await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_a_second_admin_user.id)},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "MEMBERSHIP_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_cannot_assign_retired_device(
    client: AsyncClient, org_a_admin_user: User, org_a_second_admin_user: User
) -> None:
    """STEP 35 case #26: retired device assignment must fail."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    retire_res = await client.post(
        f"/api/v1/devices/{device_id}/retire", headers={"X-CSRF-Token": csrf}
    )
    assert retire_res.status_code == 200

    assign_res = await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_a_second_admin_user.id)},
    )
    assert assign_res.status_code == 409
    assert assign_res.json()["error"]["code"] == "DEVICE_RETIRED"


@pytest.mark.asyncio
async def test_reassign_replaces_previous_assignee(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_second_admin_user: User,
    org_a_member_user: User,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_a_second_admin_user.id)},
    )
    res = await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"user_id": str(org_a_member_user.id)},
    )
    assert res.status_code == 200
    assert res.json()["assigned_user_id"] == str(org_a_member_user.id)
