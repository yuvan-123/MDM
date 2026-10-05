"""Sensitive device identifier handling (Phase 4, STEP 5 / STEP 18).

IMEI and Wi-Fi MAC are never stored or returned in full — only a SHA-256
hash (for organization-scoped dedup) and a last-4 fragment (for a human to
recognize the device) are persisted. Because there is no reversible full
value anywhere in the system, `device.view_sensitive` gates visibility of
that last-4 fragment itself — the most granular sensitive data Phase 4
retains. See docs/security/device-security.md for the full rationale.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventType, AuditLog
from app.users.models import User
from tests.conftest import _login


def _device_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Sensitive Data Test Device",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
        "imei": "35-209900-176148-1",
        "wifi_mac": "AA:BB:CC:DD:EE:FF",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_imei_and_mac_never_returned_in_full(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert "imei" not in body
    assert "wifi_mac" not in body
    assert "imei_hash" not in body
    assert "wifi_mac_hash" not in body


@pytest.mark.asyncio
async def test_privileged_viewer_sees_last4(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    """ORGANIZATION_ADMIN holds device.view_sensitive."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    assert created.json()["imei_last4"] == "1481"
    assert created.json()["wifi_mac_last4"] == "EEFF"

    device_id = created.json()["id"]
    fetched = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert fetched.json()["imei_last4"] == "1481"
    assert fetched.json()["wifi_mac_last4"] == "EEFF"


@pytest.mark.asyncio
async def test_unprivileged_viewer_does_not_see_last4(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
) -> None:
    """HELP_DESK does not hold device.view_sensitive."""
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": admin_csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    helpdesk_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": helpdesk_csrf}
    )
    assert res.status_code == 200
    assert res.json()["imei_last4"] is None
    assert res.json()["wifi_mac_last4"] is None


@pytest.mark.asyncio
async def test_sensitive_view_is_audited(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    await client.get(f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf})

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.DEVICE_SENSITIVE_DATA_VIEWED
        )
    )
    entries = result.scalars().all()
    assert len(entries) == 1
    assert entries[0].actor_user_id == org_a_admin_user.id
    assert entries[0].event_metadata is not None
    assert entries[0].event_metadata["device_id"] == device_id
    # Never leak the actual sensitive fragment into audit metadata.
    assert "imei_last4" not in entries[0].event_metadata
    assert "wifi_mac_last4" not in entries[0].event_metadata


@pytest.mark.asyncio
async def test_unprivileged_viewer_does_not_trigger_sensitive_audit(
    client: AsyncClient,
    org_a_admin_user: User,
    org_a_member_user: User,
    db_session: AsyncSession,
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    created = await client.post(
        "/api/v1/devices", headers={"X-CSRF-Token": admin_csrf}, json=_device_payload()
    )
    device_id = created.json()["id"]

    helpdesk_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    await client.get(f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": helpdesk_csrf})

    result = await db_session.execute(
        select(AuditLog).where(
            AuditLog.event_type == AuditEventType.DEVICE_SENSITIVE_DATA_VIEWED
        )
    )
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_device_without_identifiers_has_no_last4(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/devices",
        headers={"X-CSRF-Token": csrf},
        json=_device_payload(imei=None, wifi_mac=None),
    )
    assert res.status_code == 201
    assert res.json()["imei_last4"] is None
    assert res.json()["wifi_mac_last4"] is None
