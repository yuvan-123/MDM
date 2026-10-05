"""Tests for Authentication endpoints and AuthService business logic."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repository import UserRepository
from app.users.models import User, UserStatus


@pytest.mark.asyncio
async def test_successful_login(client: AsyncClient, super_admin_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "user" in data
    assert data["user"]["email"] == super_admin_user.email
    assert "csrf_token" in data

    # Check cookies set
    cookies = {c.name: c.value for c in response.cookies.jar}
    assert "access_token" in cookies
    assert "refresh_token" in cookies
    assert "csrf_token" in cookies


@pytest.mark.asyncio
async def test_login_incorrect_password(client: AsyncClient, super_admin_user: User) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "WrongPassword!"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.com", "password": "Password123!"},
    )
    assert response.status_code == 401
    body = response.json()
    # Must use same error code to prevent user enumeration
    assert body["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_disabled_user(
    client: AsyncClient, db_session: AsyncSession, super_admin_user: User
) -> None:
    user_repo = UserRepository(db_session)
    await user_repo.update_status(super_admin_user.id, UserStatus.DISABLED)
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "ACCOUNT_INACTIVE"


@pytest.mark.asyncio
async def test_account_lockout_after_failed_attempts(
    client: AsyncClient, db_session: AsyncSession, super_admin_user: User
) -> None:
    # 5 failed login attempts
    for _ in range(5):
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": super_admin_user.email, "password": "WrongPassword!"},
        )
        assert res.status_code in (401, 423)

    # 6th attempt should be locked
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    assert res.status_code == 423
    body = res.json()
    assert body["error"]["code"] == "ACCOUNT_LOCKED"


@pytest.mark.asyncio
async def test_refresh_token_rotation(client: AsyncClient, super_admin_user: User) -> None:
    # Login to get cookies
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    assert login_res.status_code == 200

    # Refresh
    refresh_res = await client.post("/api/v1/auth/refresh")
    assert refresh_res.status_code == 200
    assert "access_token" in refresh_res.cookies


@pytest.mark.asyncio
async def test_logout_clears_cookies(client: AsyncClient, super_admin_user: User) -> None:
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    assert login_res.status_code == 200
    csrf_token = login_res.json()["csrf_token"]

    logout_res = await client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout_res.status_code == 204

    # Subsequent /me call should fail with 401
    me_res = await client.get("/api/v1/auth/me")
    assert me_res.status_code == 401


@pytest.mark.asyncio
async def test_forgot_password_no_user_enumeration(client: AsyncClient) -> None:
    # Request reset for email that does NOT exist
    response = await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "doesnotexist@test.com"},
    )
    # Must return 204 (same as existing user)
    assert response.status_code == 204
