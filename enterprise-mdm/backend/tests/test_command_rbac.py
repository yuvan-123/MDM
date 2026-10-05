"""Device command RBAC — permission gating per role (Phase 6).

Role capabilities under test (see app/rbac/permissions.py BUILT_IN_ROLES):
- ORGANIZATION_ADMIN: full command administration (covered incidentally by
  test_commands.py's happy-path tests; not re-asserted exhaustively here).
- SECURITY_ADMIN: full command set (lock/wipe/reboot/refresh/policy_sync/
  cancel) plus read — security-relevant actions are its whole purpose.
- HELP_DESK: device.lock (pre-existing) + device.command.refresh only —
  cannot reboot/wipe/policy_sync/cancel.
- AUDITOR: read-only — device.read covers list/get; every command POST is
  403.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.users.models import User
from tests.conftest import _login
from tests.test_commands import _enroll_device


@pytest.mark.asyncio
async def test_help_desk_can_lock_and_refresh_but_not_reboot_wipe_policy_sync_cancel(
    client: AsyncClient, org_a_admin_user: User, org_a_member_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, admin_csrf)

    helpdesk_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")

    lock_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={},
    )
    assert lock_res.status_code == 201, lock_res.text

    refresh_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/refresh",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={},
    )
    assert refresh_res.status_code == 201, refresh_res.text

    reboot_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/reboot",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={},
    )
    assert reboot_res.status_code == 403
    assert reboot_res.json()["error"]["code"] == "PERMISSION_DENIED"

    wipe_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={"confirm": True, "idempotency_key": "help-desk-wipe-attempt"},
    )
    assert wipe_res.status_code == 403

    policy_sync_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/policy-sync",
        headers={"X-CSRF-Token": helpdesk_csrf},
        json={},
    )
    assert policy_sync_res.status_code == 403

    cancel_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/{lock_res.json()['id']}/cancel",
        headers={"X-CSRF-Token": helpdesk_csrf},
    )
    assert cancel_res.status_code == 403


@pytest.mark.asyncio
async def test_security_admin_has_full_command_access(
    client: AsyncClient, org_a_admin_user: User, org_a_security_admin_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, admin_csrf)

    sec_csrf = await _login(
        client, org_a_security_admin_user.email, "OrgASecurityAdminPass123!"
    )

    commands: list[tuple[str, dict[str, object]]] = [
        ("lock", {}),
        ("reboot", {}),
        ("policy-sync", {}),
        ("refresh", {}),
    ]
    for path, body in commands:
        res = await client.post(
            f"/api/v1/devices/{device_id}/commands/{path}",
            headers={"X-CSRF-Token": sec_csrf},
            json=body,
        )
        assert res.status_code == 201, (path, res.text)

    wipe_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": sec_csrf},
        json={"confirm": True, "idempotency_key": "sec-admin-wipe"},
    )
    assert wipe_res.status_code == 201, wipe_res.text


@pytest.mark.asyncio
async def test_auditor_can_read_but_not_issue_commands(
    client: AsyncClient, org_a_admin_user: User, org_a_auditor_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, admin_csrf)
    created = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock",
        headers={"X-CSRF-Token": admin_csrf},
        json={},
    )
    command_id = created.json()["id"]

    auditor_csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")

    list_res = await client.get(
        f"/api/v1/devices/{device_id}/commands", headers={"X-CSRF-Token": auditor_csrf}
    )
    assert list_res.status_code == 200

    get_res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{command_id}",
        headers={"X-CSRF-Token": auditor_csrf},
    )
    assert get_res.status_code == 200

    lock_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock",
        headers={"X-CSRF-Token": auditor_csrf},
        json={},
    )
    assert lock_res.status_code == 403
    assert lock_res.json()["error"]["code"] == "PERMISSION_DENIED"
