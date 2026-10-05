"""Tests for Role-Based Access Control (RBAC) authorization logic."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.users.models import User


@pytest.mark.asyncio
async def test_super_admin_can_access_users_list(
    client: AsyncClient, super_admin_user: User
) -> None:
    # Log in as super admin
    await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )

    response = await client.get("/api/v1/users")
    assert response.status_code == 200
    users = response.json()
    assert isinstance(users, list)


@pytest.mark.asyncio
async def test_helpdesk_cannot_create_user(
    client: AsyncClient, helpdesk_user: User
) -> None:
    # Log in as helpdesk user (only has user.read, device.read, device.lock)
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": helpdesk_user.email, "password": "HelpDeskPass123!"},
    )
    csrf_token = login_res.json()["csrf_token"]

    # Attempt to create a user (requires user.create)
    response = await client.post(
        "/api/v1/users",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "email": "newuser@test.com",
            "password": "NewUserPassword123!",
            "first_name": "New",
            "last_name": "User",
        },
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_auditor_cannot_modify_user_status(
    client: AsyncClient, auditor_user: User, super_admin_user: User
) -> None:
    # Log in as auditor (read-only)
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": auditor_user.email, "password": "AuditorPass123!"},
    )
    csrf_token = login_res.json()["csrf_token"]

    # Attempt status modification (requires user.update)
    response = await client.patch(
        f"/api/v1/users/{super_admin_user.id}/status",
        headers={"X-CSRF-Token": csrf_token},
        json={"status": "SUSPENDED"},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_unauthenticated_request_denied(client: AsyncClient) -> None:
    response = await client.get("/api/v1/users")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "NOT_AUTHENTICATED"
