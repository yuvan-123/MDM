"""Organization membership + invitation lifecycle (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.repositories.invitation_repository import InvitationRepository
from app.repositories.rbac_repository import RBACRepository
from app.users.models import User
from tests.conftest import _CapturingEmailService, _login


def _extract_token(invite_url: str) -> str:
    return invite_url.split("token=")[1]


@pytest.mark.asyncio
async def test_invite_member_creates_pending_invitation(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    captured_emails: _CapturingEmailService,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "newmember@test.com", "role_name": "HELP_DESK"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "PENDING"
    assert len(captured_emails.invitations) == 1
    assert captured_emails.invitations[0]["to_email"] == "newmember@test.com"


@pytest.mark.asyncio
async def test_invite_member_requires_permission(
    client: AsyncClient, org_a_member_user: User, organization_a: Organization
) -> None:
    """org_a_member_user holds HELP_DESK, which has organization.members.read
    but not organization.members.create."""
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "shouldfail@test.com", "role_name": "HELP_DESK"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_invite_rejects_super_admin_role(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "wouldbeadmin@test.com", "role_name": "SUPER_ADMIN"},
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_accept_invitation_creates_new_user_and_membership(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    captured_emails: _CapturingEmailService,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    invite_res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "brandnew@test.com", "role_name": "HELP_DESK"},
    )
    assert invite_res.status_code == 201
    raw_token = _extract_token(captured_emails.invitations[0]["invite_url"])

    accept_res = await client.post(
        "/api/v1/organizations/invitations/accept",
        json={
            "token": raw_token,
            "password": "BrandNewPass123!",
            "first_name": "Brand",
            "last_name": "New",
        },
    )
    assert accept_res.status_code == 200, accept_res.text
    data = accept_res.json()
    assert data["status"] == "ACTIVE"
    assert data["user_email"] == "brandnew@test.com"
    assert any(r["role_name"] == "HELP_DESK" for r in data["roles"])

    # The new account can now log in on its own.
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": "brandnew@test.com", "password": "BrandNewPass123!"},
    )
    assert login_res.status_code == 200


@pytest.mark.asyncio
async def test_accept_invitation_for_existing_user(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    organization_a: Organization,
    captured_emails: _CapturingEmailService,
) -> None:
    """org_b_admin_user already has an account (in org B) and gets invited
    into org A too — no password needed, they just accept."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    invite_res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": org_b_admin_user.email, "role_name": "AUDITOR"},
    )
    assert invite_res.status_code == 201
    raw_token = _extract_token(captured_emails.invitations[0]["invite_url"])

    accept_res = await client.post(
        "/api/v1/organizations/invitations/accept", json={"token": raw_token}
    )
    assert accept_res.status_code == 200
    assert accept_res.json()["user_id"] == str(org_b_admin_user.id)


@pytest.mark.asyncio
async def test_duplicate_membership_prevented(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    """Inviting someone who already holds an ACTIVE membership is rejected."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": org_a_admin_user.email, "role_name": "HELP_DESK"},
    )
    assert res.status_code == 409


@pytest.mark.asyncio
async def test_invitation_expiry(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """An expired invitation token cannot be accepted."""
    rbac_repo = RBACRepository(db_session)
    role = await rbac_repo.get_role_by_name("HELP_DESK")
    assert role is not None
    invitation_repo = InvitationRepository(db_session)
    await invitation_repo.create(
        organization_id=organization_a.id,
        email="expired@test.com",
        role_id=role.id,
        token_hash="a" * 64,
        expires_at=datetime.now(tz=UTC) - timedelta(hours=1),
        invited_by=org_a_admin_user.id,
    )
    await db_session.commit()

    # get_valid_by_token_hash filters on status + expiry — hashing a raw
    # token that maps to "a"*64 is not feasible, so instead verify directly
    # that the expired row is excluded from lookup.
    result = await invitation_repo.get_valid_by_token_hash("a" * 64)
    assert result is None


@pytest.mark.asyncio
async def test_invitation_reuse_prevented(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    captured_emails: _CapturingEmailService,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await client.post(
        f"/api/v1/organizations/{organization_a.id}/members",
        headers={"X-CSRF-Token": csrf},
        json={"email": "onceonly@test.com", "role_name": "HELP_DESK"},
    )
    raw_token = _extract_token(captured_emails.invitations[0]["invite_url"])

    first = await client.post(
        "/api/v1/organizations/invitations/accept",
        json={"token": raw_token, "password": "OnceOnlyPass123!"},
    )
    assert first.status_code == 200

    second = await client.post(
        "/api/v1/organizations/invitations/accept",
        json={"token": raw_token, "password": "OnceOnlyPass123!"},
    )
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_remove_member_revokes_org_access(
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

    remove_res = await client.delete(
        f"/api/v1/organizations/{organization_a.id}/members/{member_row['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert remove_res.status_code == 204

    member_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    denied = await client.get(
        f"/api/v1/organizations/{organization_a.id}", headers={"X-CSRF-Token": member_csrf}
    )
    assert denied.status_code == 403
    # The membership row still exists (status=REMOVED, kept for audit
    # history rather than hard-deleted) — resolve_organization_context
    # finds it and reports MEMBERSHIP_SUSPENDED (its check is "not ACTIVE",
    # covering INVITED/SUSPENDED/REMOVED alike), not ORGANIZATION_ACCESS_DENIED
    # (which is reserved for "no membership row at all").
    assert denied.json()["error"]["code"] == "MEMBERSHIP_SUSPENDED"


@pytest.mark.asyncio
async def test_suspend_member(
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

    suspend_res = await client.patch(
        f"/api/v1/organizations/{organization_a.id}/members/{member_row['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "SUSPENDED"},
    )
    assert suspend_res.status_code == 200
    assert suspend_res.json()["status"] == "SUSPENDED"

    member_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    denied = await client.get(
        f"/api/v1/organizations/{organization_a.id}", headers={"X-CSRF-Token": member_csrf}
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "MEMBERSHIP_SUSPENDED"
