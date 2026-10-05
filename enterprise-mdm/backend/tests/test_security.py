"""Security-critical test cases (Phase 2 requirement #29)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.users.models import User, UserStatus


@pytest.mark.asyncio
async def test_password_hash_never_returned_in_api_response(
    client: AsyncClient, super_admin_user: User
) -> None:
    # 1. Login response
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    assert login_res.status_code == 200
    assert "password_hash" not in str(login_res.json())

    # 2. GET /me response
    me_res = await client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert "password_hash" not in str(me_res.json())

    # 3. GET /users list
    users_res = await client.get("/api/v1/users")
    assert users_res.status_code == 200
    assert "password_hash" not in str(users_res.json())


@pytest.mark.asyncio
async def test_privilege_escalation_via_request_json_ignored(
    client: AsyncClient, super_admin_user: User, helpdesk_user: User
) -> None:
    # An admin authorized to update users (user.update) attempts to smuggle
    # role/permission fields through the update payload (mass-assignment).
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    csrf_token = login_res.json()["csrf_token"]

    # Malicious payload trying to inject role/permissions directly
    response = await client.patch(
        f"/api/v1/users/{helpdesk_user.id}",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "first_name": "Attacker",
            "role": "SUPER_ADMIN",
            "permissions": ["device.wipe", "user.delete"],
        },
    )
    # The endpoint only updates names (ignores role/permissions fields)
    assert response.status_code == 200
    data = response.json()
    assert data["first_name"] == "Attacker"
    assert "role" not in data
    assert "permissions" not in data

    # Verify the target user still cannot perform a super_admin-only action
    helpdesk_login = await client.post(
        "/api/v1/auth/login",
        json={"email": helpdesk_user.email, "password": "HelpDeskPass123!"},
    )
    helpdesk_csrf = helpdesk_login.json()["csrf_token"]
    create_res = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={
            "email": "shouldfail@test.com",
            "password": "Password123!",
            "first_name": "Fail",
            "last_name": "Test",
        },
    )
    assert create_res.status_code == 403


@pytest.mark.asyncio
async def test_disabled_user_cannot_access_authenticated_endpoints(
    client: AsyncClient, db_session: AsyncSession, super_admin_user: User
) -> None:
    # Login to establish session
    await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )

    # Admin is now disabled
    user_repo = UserRepository(db_session)
    session_repo = SessionRepository(db_session)
    await user_repo.update_status(super_admin_user.id, UserStatus.DISABLED)
    await session_repo.revoke_all_for_user(super_admin_user.id)
    await db_session.commit()

    # Attempt to access protected endpoint
    response = await client.get("/api/v1/auth/me")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_revoked_session_cannot_access_protected_endpoints(
    client: AsyncClient, super_admin_user: User
) -> None:
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    csrf_token = login_res.json()["csrf_token"]

    # Logout revokes session
    await client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf_token})

    # Protected call fails
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401
