"""Device RBAC — permission gating per role (Phase 4).

Role capabilities under test (see app/rbac/permissions.py BUILT_IN_ROLES):
- ORGANIZATION_ADMIN: full device administration.
- HELP_DESK: device.read, device.assign, device.unassign only — cannot
  create/update/block/retire.
- AUDITOR: device.read only — cannot mutate anything.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.users.models import User
from tests.conftest import _login


def _device_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "RBAC Test Device",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_help_desk_cannot_create_device(
    client: AsyncClient, org_a_member_user: User
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_help_desk_can_read_devices(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": admin_csrf}, json=_device_payload()
    )

    helpdesk_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.get("/api/v1/devices", headers={"X-CSRF-Token": helpdesk_csrf})
    assert res.status_code == 200
    assert res.json()["total"] == 1


@pytest.mark.asyncio
async def test_help_desk_can_assign_but_not_block(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
    org_a_second_admin_user: User,
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": admin_csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    helpdesk_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    assign_res = await client.post(
        f"/api/v1/devices/{device_id}/assign",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={"user_id": str(org_a_second_admin_user.id)},
    )
    assert assign_res.status_code == 200, assign_res.text

    block_res = await client.post(
        f"/api/v1/devices/{device_id}/block", headers={"X-CSRF-Token": helpdesk_csrf}
    )
    assert block_res.status_code == 403
    assert block_res.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_auditor_can_read_but_not_mutate(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_auditor_user: User,
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": admin_csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    auditor_csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")
    read_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": auditor_csrf}
    )
    assert read_res.status_code == 200

    update_res = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers={"X-CSRF-Token": auditor_csrf},
        json={"display_name": "Hijacked"},
    )
    assert update_res.status_code == 403

    retire_res = await client.post(
        f"/api/v1/devices/{device_id}/retire", headers={"X-CSRF-Token": auditor_csrf}
    )
    assert retire_res.status_code == 403


@pytest.mark.asyncio
async def test_org_admin_can_block_and_retire(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    block_res = await client.post(
        f"/api/v1/devices/{device_id}/block", headers={"X-CSRF-Token": csrf}
    )
    assert block_res.status_code == 200
    assert block_res.json()["status"] == "BLOCKED"

    retire_res = await client.post(
        f"/api/v1/devices/{device_id}/retire", headers={"X-CSRF-Token": csrf}
    )
    assert retire_res.status_code == 200  # a BLOCKED device may still be retired directly
    assert retire_res.json()["status"] == "RETIRED"


@pytest.mark.asyncio
async def test_unauthorized_delete_denied(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_auditor_user: User,
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": admin_csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    auditor_csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")
    res = await client.delete(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": auditor_csrf}
    )
    assert res.status_code == 403
