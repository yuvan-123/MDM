"""Mandatory cross-tenant isolation tests (Phase 3, spec requirement #28).

Two organizations, two users, each admin of exactly one org. Proves that
neither can read, modify, or reach into the other's data, and documents
the dual response-code policy: 403 ORGANIZATION_ACCESS_DENIED for direct
organization access by a non-member, vs. 404 NOT_FOUND for a nested
resource (membership) that exists but belongs to a different organization
than the one already authorized in the request path.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.repositories.membership_repository import MembershipRepository
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_cross_tenant_get_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/organizations/{organization_b.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_cross_tenant_patch_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.patch(
        f"/api/v1/organizations/{organization_b.id}",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Hostile Takeover"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_cross_tenant_members_list_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/organizations/{organization_b.id}/members", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_cross_tenant_membership_invite_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    """org A's admin cannot invite anyone into org B."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_b.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "intruder@test.com", "role_name": "HELP_DESK"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_cross_tenant_nested_membership_returns_404_not_403(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    """org A's admin IS authorized for org A (the path org_id), but the
    specific membership_id they pass belongs to org B. This must 404, not
    403 — the tenant boundary is already satisfied at the org level; the
    repository's get_for_organization(org_id, membership_id) filter simply
    finds no such row and correctly refuses to leak that it belongs to a
    different tenant."""
    membership_repo = MembershipRepository(db_session)
    org_b_membership = await membership_repo.get_by_user_and_org(
        org_b_admin_user.id, organization_b.id
    )
    assert org_b_membership is not None

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}/members/{org_b_membership.id}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "SUSPENDED"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_org_a_admin_cannot_modify_org_b_membership_roles(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    membership_repo = MembershipRepository(db_session)
    org_b_membership = await membership_repo.get_by_user_and_org(
        org_b_admin_user.id, organization_b.id
    )
    assert org_b_membership is not None

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members/{org_b_membership.id}/roles",
        headers={"X-CSRF-Token": csrf},
        json={"role_name": "AUDITOR"},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_auditor_in_org_a_cannot_see_org_b(
    client: AsyncClient,
    org_a_member_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.get(
        f"/api/v1/organizations/{organization_b.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_can_access_both_organizations(
    client: AsyncClient,
    super_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    res_a = await client.get(
        f"/api/v1/organizations/{organization_a.id}", headers={"X-CSRF-Token": csrf}
    )
    res_b = await client.get(
        f"/api/v1/organizations/{organization_b.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res_a.status_code == 200
    assert res_b.status_code == 200


@pytest.mark.asyncio
async def test_org_list_scoped_to_membership(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    """GET /organizations for a non-super-admin returns only their own orgs."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get("/api/v1/organizations", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    org_ids = {o["id"] for o in res.json()}
    assert str(organization_a.id) in org_ids
    assert str(organization_b.id) not in org_ids
