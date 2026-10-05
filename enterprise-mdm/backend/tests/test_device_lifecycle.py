"""Device lifecycle transitions (Phase 4, STEP 9 / STEP 34).

    NOT_ENROLLED
          v
        ACTIVE <-> INACTIVE <-> LOST         (generic PATCH, non-privileged)
          |  \\_____________/
          v
       BLOCKED --unblock--> ACTIVE           (device.block / device.unblock)
          |
          v
       RETIRED                               (device.retire) -- terminal
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.users.models import User
from tests.conftest import _login


def _device_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Lifecycle Test Device",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
    }
    payload.update(overrides)
    return payload


async def _create_device(client: AsyncClient, csrf: str) -> str:
    res = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    assert res.status_code == 201
    device_id: str = res.json()["id"]
    return device_id


@pytest.mark.asyncio
async def test_generic_update_allows_active_inactive_lost(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id = await _create_device(client, csrf)

    to_lost = await client.patch(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}, json={"status": "LOST"}
    )
    assert to_lost.status_code == 200
    assert to_lost.json()["status"] == "LOST"

    to_active = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "ACTIVE"},
    )
    assert to_active.status_code == 200
    assert to_active.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_generic_update_cannot_set_blocked_or_retired(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    """BLOCKED/RETIRED are only reachable via their dedicated, separately
    permissioned endpoints — never through the generic PATCH."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id = await _create_device(client, csrf)

    res = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers={"X-CSRF-Token": csrf},
        json={"status": "BLOCKED"},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


@pytest.mark.asyncio
async def test_block_then_unblock(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id = await _create_device(client, csrf)

    block_res = await client.post(
        f"/api/v1/devices/{device_id}/block", headers={"X-CSRF-Token": csrf}
    )
    assert block_res.status_code == 200
    assert block_res.json()["status"] == "BLOCKED"

    unblock_res = await client.post(
        f"/api/v1/devices/{device_id}/unblock", headers={"X-CSRF-Token": csrf}
    )
    assert unblock_res.status_code == 200
    assert unblock_res.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_cannot_unblock_a_device_that_is_not_blocked(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id = await _create_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/unblock", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"


@pytest.mark.asyncio
async def test_retire_is_terminal(client: AsyncClient, org_a_admin_user: User) -> None:
    """STEP 34: RETIRED -> ACTIVE (or any transition) must fail."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id = await _create_device(client, csrf)

    retire_res = await client.post(
        f"/api/v1/devices/{device_id}/retire", headers={"X-CSRF-Token": csrf}
    )
    assert retire_res.status_code == 200
    assert retire_res.json()["status"] == "RETIRED"

    re_retire = await client.post(
        f"/api/v1/devices/{device_id}/retire", headers={"X-CSRF-Token": csrf}
    )
    assert re_retire.status_code == 409
    assert re_retire.json()["error"]["code"] == "INVALID_LIFECYCLE_TRANSITION"

    patch_active = await client.patch(
        f"/api/v1/devices/{device_id}",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "still retired"},
    )
    assert patch_active.status_code == 200
    assert patch_active.json()["status"] == "RETIRED"

    block_after_retire = await client.post(
        f"/api/v1/devices/{device_id}/block", headers={"X-CSRF-Token": csrf}
    )
    assert block_after_retire.status_code == 409

    unblock_after_retire = await client.post(
        f"/api/v1/devices/{device_id}/unblock", headers={"X-CSRF-Token": csrf}
    )
    assert unblock_after_retire.status_code == 409


@pytest.mark.asyncio
async def test_invalid_platform_value_rejected_by_schema(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    """DevicePlatform is a closed enum — arbitrary strings are rejected at
    the schema layer (STEP 4: "Do not allow arbitrary strings")."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(platform="WINDOWS_PHONE"),
    )
    assert res.status_code == 422
