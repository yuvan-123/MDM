"""Multi-org-per-user scenarios and tenant-context resolution (Phase 3)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import MembershipStatus, Organization
from app.rbac import permissions as p
from app.repositories.membership_repository import MembershipRepository
from app.repositories.rbac_repository import RBACRepository
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_user_can_hold_different_roles_in_two_organizations(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    """org_a_admin_user is ORGANIZATION_ADMIN in org A. Also grant them
    AUDITOR in org B and verify their effective permission set differs
    per-organization."""
    membership_repo = MembershipRepository(db_session)
    rbac_repo = RBACRepository(db_session)

    await membership_repo.create(
        organization_id=organization_b.id,
        user_id=org_a_admin_user.id,
        status=MembershipStatus.ACTIVE,
    )
    auditor_role = await rbac_repo.get_role_by_name(p.AUDITOR)
    assert auditor_role is not None
    await rbac_repo.assign_role(
        user_id=org_a_admin_user.id,
        role_id=auditor_role.id,
        granted_by=None,
        organization_id=organization_b.id,
    )
    await db_session.commit()

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    # Full admin rights in org A: can invite members.
    res_a = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "someone@test.com", "role_name": "HELP_DESK"},
    )
    assert res_a.status_code == 201

    # Read-only (AUDITOR) in org B: cannot invite members there.
    res_b = await client.post(
        f"/api/v1/organizations/{organization_b.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "someoneelse@test.com", "role_name": "HELP_DESK"},
    )
    assert res_b.status_code == 403

    # But CAN read org B (AUDITOR has organization.read).
    res_b_read = await client.get(
        f"/api/v1/organizations/{organization_b.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res_b_read.status_code == 200


@pytest.mark.asyncio
async def test_select_organization_validates_membership(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/auth/select-organization",
        headers={"X-CSRF-Token": csrf},
        json={"organization_id": str(organization_a.id)},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["organization"]["id"] == str(organization_a.id)
    assert data["is_platform_super_admin"] is False
    assert "organization.members.create" in data["permissions"]


@pytest.mark.asyncio
async def test_select_organization_rejects_non_member_org(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    organization_b: Organization,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/auth/select-organization",
        headers={"X-CSRF-Token": csrf},
        json={"organization_id": str(organization_b.id)},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_header_based_context_auto_defaults_for_single_org_user(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    """org_a_admin_user belongs to exactly one org — GET /organizations/{id}/members
    without an explicit X-Organization-ID header still works because the
    path itself already disambiguates (get_organization_context is
    path-based, not header-based) — this test instead exercises
    /auth/select-organization with no ambiguity to confirm a single-org
    user resolves cleanly end-to-end."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/auth/select-organization",
        headers={"X-CSRF-Token": csrf},
        json={"organization_id": str(organization_a.id)},
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_nonexistent_organization_id_returns_404(
    client: AsyncClient, super_admin_user: User
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    res = await client.get(
        "/api/v1/organizations/00000000-0000-0000-0000-000000000000",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 404
