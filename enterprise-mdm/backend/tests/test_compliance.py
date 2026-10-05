"""Compliance evaluation engine (Phase 5, STEP 9-10).

Covers rule evaluation (compliant/non-compliant/unknown), policy
precedence (device > device-group > user > organization), and the
summary/non-compliant/evaluate API surface.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.devices.models import Device
from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login


async def _create_active_policy(
    client: AsyncClient, csrf: str, *, name: str, configuration: dict[str, object]
) -> str:
    created = await client.post(
        "/api/v1/policies",
        headers={"X-CSRF-Token": csrf},
        json={"name": name, "configuration": configuration},
    )
    assert created.status_code == 201, created.text
    policy_id = created.json()["id"]
    activate = await client.patch(
        f"/api/v1/policies/{policy_id}", headers={"X-CSRF-Token": csrf}, json={"status": "ACTIVE"}
    )
    assert activate.status_code == 200
    return policy_id


async def _assign(
    client: AsyncClient, csrf: str, policy_id: str, scope_type: str, scope_id: str
) -> None:
    res = await client.post(
        f"/api/v1/policies/{policy_id}/assign",
        headers={"X-CSRF-Token": csrf},
        json={"scope_type": scope_type, "scope_id": scope_id},
    )
    assert res.status_code == 200, res.text


@pytest.mark.asyncio
async def test_no_policy_assigned_is_unknown(
    client: AsyncClient, org_a_admin_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/compliance/evaluate/{device_in_org_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "UNKNOWN"
    assert res.json()["details"][0]["code"] == "NO_POLICY_ASSIGNED"


@pytest.mark.asyncio
async def test_compliant_and_non_compliant_via_enrolled_device(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    policy_id = await _create_active_policy(
        client,
        csrf,
        name="Encryption Required",
        configuration={"device_security": {"require_encryption": True}},
    )
    await _assign(client, csrf, policy_id, "ORGANIZATION", str(organization_a.id))

    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Compliance Test Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    device_id = reg_res.json()["device_id"]
    device_secret = reg_res.json()["device_secret"]

    # Not encrypted yet -> NON_COMPLIANT.
    await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={"is_encrypted": False},
    )
    eval_res = await client.post(
        f"/api/v1/compliance/evaluate/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert eval_res.status_code == 200
    assert eval_res.json()["status"] == "NON_COMPLIANT"
    assert eval_res.json()["details"][0]["outcome"] == "FAIL"

    # Now encrypted -> COMPLIANT.
    await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={"is_encrypted": True},
    )
    eval_res2 = await client.post(
        f"/api/v1/compliance/evaluate/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert eval_res2.status_code == 200
    assert eval_res2.json()["status"] == "COMPLIANT"

    detail_res = await client.get(
        f"/api/v1/compliance/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert detail_res.json()["status"] == "COMPLIANT"


@pytest.mark.asyncio
async def test_device_specific_policy_takes_precedence_over_organization(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    org_policy_id = await _create_active_policy(
        client,
        csrf,
        name="Org Policy",
        configuration={"device_security": {"require_encryption": True}},
    )
    await _assign(client, csrf, org_policy_id, "ORGANIZATION", str(organization_a.id))

    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Precedence Test Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    device_id = reg_res.json()["device_id"]
    device_secret = reg_res.json()["device_secret"]

    # No device_security requirement at the device level -> COMPLIANT even
    # though the org policy (unencrypted device) would have failed.
    device_policy_id = await _create_active_policy(
        client, csrf, name="Device Override Policy", configuration={}
    )
    await _assign(client, csrf, device_policy_id, "DEVICE", device_id)

    await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={"is_encrypted": False},
    )
    eval_res = await client.post(
        f"/api/v1/compliance/evaluate/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert eval_res.status_code == 200
    assert eval_res.json()["status"] == "COMPLIANT"
    assert eval_res.json()["policy_id"] == device_policy_id


@pytest.mark.asyncio
async def test_compliance_summary_and_non_compliant_list(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    policy_id = await _create_active_policy(
        client,
        csrf,
        name="Rooted Device Block",
        configuration={"device_security": {"block_rooted_jailbroken": True}},
    )
    await _assign(client, csrf, policy_id, "ORGANIZATION", str(organization_a.id))

    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": raw_token,
            "display_name": "Rooted Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    device_id = reg_res.json()["device_id"]
    device_secret = reg_res.json()["device_secret"]
    await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={"is_rooted": True},
    )
    await client.post(
        f"/api/v1/compliance/evaluate/{device_id}", headers={"X-CSRF-Token": csrf}
    )

    summary_res = await client.get("/api/v1/compliance/summary", headers={"X-CSRF-Token": csrf})
    assert summary_res.status_code == 200
    assert summary_res.json()["non_compliant"] >= 1
    assert summary_res.json()["total_devices"] >= 1

    non_compliant_res = await client.get(
        "/api/v1/compliance/non-compliant", headers={"X-CSRF-Token": csrf}
    )
    assert non_compliant_res.status_code == 200
    ids = {item["device_id"] for item in non_compliant_res.json()["items"]}
    assert device_id in ids


@pytest.mark.asyncio
async def test_help_desk_cannot_trigger_evaluation(
    client: AsyncClient, org_a_member_user: User, device_in_org_a: Device
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        f"/api/v1/compliance/evaluate/{device_in_org_a.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_cross_tenant_compliance_access_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    device_in_org_a: Device,
) -> None:
    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    res = await client.get(
        f"/api/v1/compliance/devices/{device_in_org_a.id}", headers={"X-CSRF-Token": b_csrf}
    )
    # Cross-tenant device lookup 404s (matches devices module's IDOR policy).
    assert res.status_code == 404
