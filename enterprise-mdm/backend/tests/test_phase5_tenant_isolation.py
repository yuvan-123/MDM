"""Explicit Phase 5 cross-tenant security checks (spec Step 17 "Security"):

    Organization A cannot access Organization B devices
    Organization A cannot access Organization B policies
    Organization A cannot access Organization B groups
    Organization A cannot use Organization B enrollment tokens

Per-module CRUD/RBAC/precedence tests live in their own test files
(test_enrollment.py, test_device_groups.py, test_policies.py,
test_compliance.py) — this file is specifically the cross-org matrix the
spec calls out by name.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login


@pytest.mark.asyncio
async def test_org_a_cannot_use_org_b_enrollment_token(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    """An enrollment token is scoped to the organization that created it —
    using a valid, unexpired, unused token from a DIFFERENT org must
    still register the device into the token's OWN organization, never
    into whichever org the request happens to resemble. There is no
    "requesting org" on this unauthenticated endpoint at all — this test
    proves the resulting device always lands in org B (the token's true
    owner), confirming organization_id is derived from the token, never
    guessable/spoofable by the caller."""
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": b_csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]

    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Cross-org attempt",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    assert reg_res.status_code == 201
    org_b_id = token_res.json()["organization_id"]
    assert reg_res.json()["device"]["organization_id"] == org_b_id

    # Org A's admin must not be able to see this device or its token.
    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id = reg_res.json()["device_id"]
    get_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": a_csrf}
    )
    assert get_res.status_code == 404

    tokens_res = await client.get(
        "/api/v1/enrollment/tokens", headers={"X-CSRF-Token": a_csrf}
    )
    token_ids = {t["id"] for t in tokens_res.json()}
    assert token_res.json()["id"] not in token_ids


@pytest.mark.asyncio
async def test_org_a_cannot_revoke_org_b_enrollment_token(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens", headers={"X-CSRF-Token": b_csrf}, json={"expires_in_hours": 24}
    )
    token_id = token_res.json()["id"]

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.delete(
        f"/api/v1/enrollment/tokens/{token_id}", headers={"X-CSRF-Token": a_csrf}
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "ENROLLMENT_TOKEN_NOT_FOUND"


@pytest.mark.asyncio
async def test_org_a_cannot_approve_org_b_device_enrollment(
    client: AsyncClient, org_a_admin_user: User, org_b_admin_user: User
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens", headers={"X-CSRF-Token": b_csrf}, json={"expires_in_hours": 24}
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Org B Pending Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    device_id = reg_res.json()["device_id"]

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/approve", headers={"X-CSRF-Token": a_csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_forged_organization_header_rejected_for_new_modules(
    client: AsyncClient, org_a_admin_user: User, organization_b: Organization
) -> None:
    """STEP 16: org_a_admin_user belongs only to organization_a. Presenting
    X-Organization-ID: organization_b must be rejected for every new
    Phase 5 resource, not just devices (already covered in Phase 4)."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    forged_headers = {"X-CSRF-Token": csrf, "X-Organization-ID": str(organization_b.id)}

    for path in ("/api/v1/device-groups", "/api/v1/policies", "/api/v1/enrollment/tokens"):
        res = await client.get(path, headers=forged_headers)
        assert res.status_code == 403, f"{path} did not reject a forged org header"
        assert res.json()["error"]["code"] == "ORGANIZATION_ACCESS_DENIED"
