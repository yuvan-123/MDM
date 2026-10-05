"""Tests for user management endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.users.models import User


@pytest.mark.asyncio
async def test_user_crud_flow(client: AsyncClient, super_admin_user: User) -> None:
    # 1. Login as Super Admin
    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": super_admin_user.email, "password": "SuperSecret123!"},
    )
    csrf_token = login_res.json()["csrf_token"]
    headers = {"X-CSRF-Token": csrf_token}

    # 2. Create new user
    create_res = await client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "orgadmin@test.com",
            "password": "SecurePassword123!",
            "first_name": "Org",
            "last_name": "Admin",
            "initial_role": "ORGANIZATION_ADMIN",
        },
    )
    assert create_res.status_code == 201
    created_user = create_res.json()
    new_user_id = created_user["id"]

    # 3. Get user details
    get_res = await client.get(f"/api/v1/users/{new_user_id}")
    assert get_res.status_code == 200
    detail = get_res.json()
    assert detail["email"] == "orgadmin@test.com"
    assert len(detail["roles"]) == 1
    assert detail["roles"][0]["role_name"] == "ORGANIZATION_ADMIN"

    # 4. Update user names
    patch_res = await client.patch(
        f"/api/v1/users/{new_user_id}",
        headers=headers,
        json={"first_name": "UpdatedOrg"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["first_name"] == "UpdatedOrg"

    # 5. Change user status to SUSPENDED
    status_res = await client.patch(
        f"/api/v1/users/{new_user_id}/status",
        headers=headers,
        json={"status": "SUSPENDED"},
    )
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "SUSPENDED"
