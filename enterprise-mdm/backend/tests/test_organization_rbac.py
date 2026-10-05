"""Org-scoped role assignment, self-role-modification, and privilege
escalation guards (Phase 3, spec requirement #26)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.repositories.membership_repository import MembershipRepository
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_org_admin_can_assign_role_to_other_member(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
    organization_a: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    list_res = await client.get(
        f"/api/v1/organizations/{organization_a.id}/members", headers={"X-CSRF-Token": csrf}
    )
    member_row = next(
        m for m in list_res.json() if m["user_id"] == str(org_a_member_user.id)
    )

    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members/{member_row['id']}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_name": "AUDITOR"},
    )
    assert res.status_code == 201, res.text
    role_names = {r["role_name"] for r in res.json()["roles"]}
    assert {"HELP_DESK", "AUDITOR"} <= role_names


@pytest.mark.asyncio
async def test_self_role_modification_denied(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    """An org admin cannot change their own role assignment."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    list_res = await client.get(
        f"/api/v1/organizations/{organization_a.id}/members", headers={"X-CSRF-Token": csrf}
    )
    own_row = next(
        m for m in list_res.json() if m["user_id"] == str(org_a_admin_user.id)
    )

    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members/{own_row['id']}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_name": "AUDITOR"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "SELF_ROLE_MODIFICATION_DENIED"


@pytest.mark.asyncio
async def test_org_admin_cannot_grant_super_admin(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
    organization_a: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    list_res = await client.get(
        f"/api/v1/organizations/{organization_a.id}/members", headers={"X-CSRF-Token": csrf}
    )
    member_row = next(
        m for m in list_res.json() if m["user_id"] == str(org_a_member_user.id)
    )

    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members/{member_row['id']}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_name": "SUPER_ADMIN"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_ROLE"


@pytest.mark.asyncio
async def test_helpdesk_member_cannot_assign_roles(
    client: AsyncClient,
    org_a_member_user: User,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """org_a_member_user (HELP_DESK) lacks organization.members.update."""
    membership_repo = MembershipRepository(db_session)
    admin_membership = await membership_repo.get_by_user_and_org(
        org_a_admin_user.id, organization_a.id
    )
    assert admin_membership is not None

    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members/{admin_membership.id}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_name": "AUDITOR"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_remove_org_role(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
    organization_a: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    list_res = await client.get(
        f"/api/v1/organizations/{organization_a.id}/members", headers={"X-CSRF-Token": csrf}
    )
    member_row = next(
        m for m in list_res.json() if m["user_id"] == str(org_a_member_user.id)
    )
    helpdesk_role_id = next(
        r["id"] for r in member_row["roles"] if r["role_name"] == "HELP_DESK"
    )

    res = await client.delete(
        f"/api/v1/organizations/{organization_a.id}/members/{member_row['id']}/roles/{helpdesk_role_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 204

    list_res_after = await client.get(
        f"/api/v1/organizations/{organization_a.id}/members", headers={"X-CSRF-Token": csrf}
    )
    member_row_after = next(
        m for m in list_res_after.json() if m["user_id"] == str(org_a_member_user.id)
    )
    assert member_row_after["roles"] == []


@pytest.mark.asyncio
async def test_super_admin_bypasses_org_membership_for_role_assignment(
    client: AsyncClient,
    super_admin_user: User,
    org_a_member_user: User,
    organization_a: Organization,
) -> None:
    """SUPER_ADMIN is not a member of organization_a at all, but can still
    manage its roles via the platform bypass."""
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    list_res = await client.get(
        f"/api/v1/organizations/{organization_a.id}/members", headers={"X-CSRF-Token": csrf}
    )
    assert list_res.status_code == 200
    member_row = next(
        m for m in list_res.json() if m["user_id"] == str(org_a_member_user.id)
    )

    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members/{member_row['id']}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_name": "AUDITOR"},
    )
    assert res.status_code == 201


@pytest.mark.asyncio
async def test_platform_permissions_unaffected_by_org_scoped_roles(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    """Regression guard for the cross-tenant leak this phase fixed:
    org_a_admin_user holds ORGANIZATION_ADMIN (which includes user.create)
    scoped ONLY to organization_a. Their PLATFORM permission set must NOT
    include user.create — hitting the platform /api/v1/users endpoint must
    still be denied."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "shouldstillfail@test.com",
            "password": "Password123!",
            "first_name": "Should",
            "last_name": "Fail",
        },
    )
    assert res.status_code == 403
