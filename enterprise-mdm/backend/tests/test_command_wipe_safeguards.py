"""Wipe-specific safeguards (Phase 6) — see
docs/security/phase-6-security-review.md.

Wipe carries stricter validation than every other command type:
`confirm` must be explicitly true, and `idempotency_key` is REQUIRED (only
optional elsewhere) so a client retry can never double-dispatch a
destructive command. A dispatched wipe also immediately clears the
device's heartbeat credential rather than waiting for a completion signal
this codebase may never receive from a real provider.

Phase 8: dispatch happens off the issuing request (see
docs/architecture/background-jobs.md §7), so tests that need the actual
dispatch outcome (credential revocation, provider_command_id) call
`tests.conftest.dispatch_pending_command` immediately after creation,
exactly what app/workers/loop.py's worker would do for one claimed
COMMAND_DISPATCH job.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.users.models import User
from tests.conftest import _login, dispatch_pending_command
from tests.test_commands import _enroll_device


@pytest.mark.asyncio
async def test_wipe_missing_confirm_rejected(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": "no-confirm-key"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_wipe_missing_idempotency_key_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_wipe_retry_with_same_key_returns_same_command_not_a_duplicate(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    first = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True, "idempotency_key": "retry-wipe-key"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["status"] == "PENDING"

    dispatched = await dispatch_pending_command(db_session, organization_a.id, first.json()["id"])
    assert dispatched.status.value == "COMPLETED"

    # The retry must find the already-dispatched row via its idempotency
    # key and return it unchanged — never re-enqueue a second dispatch.
    second = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True, "idempotency_key": "retry-wipe-key"},
    )
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["status"] == "COMPLETED"
    assert second.json()["provider_command_id"] == dispatched.provider_command_id

    history = await client.get(
        f"/api/v1/devices/{device_id}/commands?status=COMPLETED",
        headers={"X-CSRF-Token": csrf},
    )
    wipe_commands = [c for c in history.json()["items"] if c["command_type"] == "WIPE"]
    assert len(wipe_commands) == 1


@pytest.mark.asyncio
async def test_wipe_clears_device_credential_immediately(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, device_secret = await _enroll_device(client, csrf)

    # Credential works before the wipe.
    pre_wipe = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert pre_wipe.status_code == 200, pre_wipe.text

    wipe_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/wipe",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True, "idempotency_key": "credential-clear-key"},
    )
    assert wipe_res.status_code == 201, wipe_res.text

    dispatched = await dispatch_pending_command(
        db_session, organization_a.id, wipe_res.json()["id"]
    )
    assert dispatched.status.value == "COMPLETED"

    post_wipe = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert post_wipe.status_code == 401
    assert post_wipe.json()["error"]["code"] == "DEVICE_CREDENTIAL_INVALID"
