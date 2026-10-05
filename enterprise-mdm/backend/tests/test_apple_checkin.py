"""Apple MDM check-in / command-queue endpoints (Phase 9). See
app/platform/apple/checkin.py and app/platform/apple/router.py."""

from __future__ import annotations

import plistlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.devices.enums import DevicePlatform, DeviceType, EnrollmentStatus, OwnershipType
from app.organizations.models import Organization
from app.platform.apple.checkin import (
    build_command_plist,
    empty_dequeue_plist,
    extract_push_token,
    map_status_to_event_type,
    parse_plist,
    require_udid,
)
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from tests.conftest import _login
from tests.test_provider_events import _command_service

# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_parse_plist_round_trips() -> None:
    body = plistlib.dumps({"UDID": "ABC123", "MessageType": "Authenticate"})
    assert parse_plist(body) == {"UDID": "ABC123", "MessageType": "Authenticate"}


def test_parse_plist_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_plist(b"not a plist")


def test_require_udid_rejects_missing() -> None:
    with pytest.raises(ValueError):
        require_udid({"MessageType": "Authenticate"})


def test_extract_push_token_decodes_data_bytes() -> None:
    assert extract_push_token({"Token": b"\x01\x02\xff"}) == "0102ff"


def test_extract_push_token_missing_is_none() -> None:
    assert extract_push_token({}) is None


def test_build_command_plist_maps_lock() -> None:
    body = build_command_plist("uuid-1", "LOCK", {"message": "hi"})
    doc = plistlib.loads(body)
    assert doc["CommandUUID"] == "uuid-1"
    assert doc["Command"]["RequestType"] == "DeviceLock"
    assert doc["Command"]["Message"] == "hi"


def test_build_command_plist_rejects_unmapped_type() -> None:
    with pytest.raises(ValueError):
        build_command_plist("uuid-1", "POLICY_SYNC", {})


def test_empty_dequeue_plist_is_empty_dict() -> None:
    assert plistlib.loads(empty_dequeue_plist()) == {}


def test_map_status_to_event_type() -> None:
    assert map_status_to_event_type("Acknowledged") == "COMMAND_COMPLETED"
    assert map_status_to_event_type("Error") == "COMMAND_FAILED"
    assert map_status_to_event_type("CommandFormatError") == "COMMAND_FAILED"
    assert map_status_to_event_type("NotNow") is None


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


async def _linked_enrolled_device(
    db_session: AsyncSession, organization: Organization, udid: str = "FAKE-UDID-1"
):
    device_repo = DeviceRepository(db_session)
    device = await device_repo.create_for_organization(
        organization.id,
        display_name="Apple Checkin Test Device",
        platform=DevicePlatform.IOS,
        device_type=DeviceType.PHONE,
        ownership_type=OwnershipType.CORPORATE,
    )
    await device_repo.update_for_organization(device, enrollment_status=EnrollmentStatus.ENROLLED)
    await ProviderDeviceLinkRepository(db_session).create(
        organization.id, provider_name="apple", provider_device_id=udid, device_id=device.id
    )
    await db_session.commit()
    return device


@pytest.mark.asyncio
async def test_checkin_unlinked_udid_is_404(
    client: AsyncClient, organization_a: Organization
) -> None:
    body = plistlib.dumps({"UDID": "UNKNOWN-UDID", "MessageType": "Authenticate"})
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/checkin", content=body
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "APPLE_DEVICE_NOT_LINKED"


@pytest.mark.asyncio
async def test_checkin_authenticate_for_linked_device_succeeds(
    client: AsyncClient, db_session: AsyncSession, organization_a: Organization
) -> None:
    await _linked_enrolled_device(db_session, organization_a, udid="AUTH-UDID-1")
    body = plistlib.dumps({"UDID": "AUTH-UDID-1", "MessageType": "Authenticate"})
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/checkin", content=body
    )
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_checkin_token_update_stores_push_token_without_corrupting_udid(
    client: AsyncClient, db_session: AsyncSession, organization_a: Organization
) -> None:
    """Phase 10 regression test for a real Phase 9 bug: TokenUpdate must
    populate `apple_push_token`, never overwrite `provider_device_id` (the
    UDID) — corrupting the UDID would break this device's own resolution
    key on the very next check-in."""
    device = await _linked_enrolled_device(db_session, organization_a, udid="TOKEN-UDID-1")
    body = plistlib.dumps(
        {"UDID": "TOKEN-UDID-1", "MessageType": "TokenUpdate", "Token": b"\xaa\xbb\xcc"}
    )
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/checkin", content=body
    )
    assert res.status_code == 200

    link = await ProviderDeviceLinkRepository(db_session).get_link_for_device(
        organization_a.id, device.id, "apple"
    )
    assert link is not None
    assert link.apple_push_token == "aabbcc"
    assert link.provider_device_id == "TOKEN-UDID-1"  # unchanged — still the UDID


@pytest.mark.asyncio
async def test_connect_returns_empty_when_nothing_queued(
    client: AsyncClient, db_session: AsyncSession, organization_a: Organization
) -> None:
    await _linked_enrolled_device(db_session, organization_a, udid="CONNECT-UDID-1")
    body = plistlib.dumps({"UDID": "CONNECT-UDID-1"})
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/connect", content=body
    )
    assert res.status_code == 200
    assert plistlib.loads(res.content) == {}


@pytest.mark.asyncio
async def test_connect_dequeues_the_oldest_sent_command(
    client: AsyncClient,
    db_session: AsyncSession,
    organization_a: Organization,
    org_a_admin_user: User,
) -> None:

    device = await _linked_enrolled_device(db_session, organization_a, udid="DEQUEUE-UDID-1")
    command_service = _command_service(db_session)
    command = await command_service.create_command(
        organization_a.id,
        device.id,
        command_type=DeviceCommandType.LOCK,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()
    # Apple's real provider isn't configured in this environment — dispatch
    # against Mock instead so the command reaches a terminal state quickly,
    # then force it back to SENT to simulate "a real Apple dispatch already
    # put this in flight," which is what /connect is meant to serve.
    command = await command_service.process_dispatch_job(organization_a.id, command.id)
    await DeviceCommandRepository(db_session).update_for_organization(
        command, status=DeviceCommandStatus.SENT, provider_command_id="real-apple-cmd-uuid"
    )
    await db_session.commit()

    body = plistlib.dumps({"UDID": "DEQUEUE-UDID-1"})
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/connect", content=body
    )
    assert res.status_code == 200
    doc = plistlib.loads(res.content)
    assert doc["CommandUUID"] == "real-apple-cmd-uuid"
    assert doc["Command"]["RequestType"] == "DeviceLock"


@pytest.mark.asyncio
async def test_connect_status_acknowledged_completes_the_command(
    client: AsyncClient,
    db_session: AsyncSession,
    organization_a: Organization,
    org_a_admin_user: User,
) -> None:

    device = await _linked_enrolled_device(db_session, organization_a, udid="STATUS-UDID-1")
    command_service = _command_service(db_session)
    command = await command_service.create_command(
        organization_a.id,
        device.id,
        command_type=DeviceCommandType.LOCK,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()
    command = await command_service.process_dispatch_job(organization_a.id, command.id)
    await DeviceCommandRepository(db_session).update_for_organization(
        command, status=DeviceCommandStatus.SENT, provider_command_id="ack-cmd-uuid"
    )
    await db_session.commit()

    body = plistlib.dumps(
        {"UDID": "STATUS-UDID-1", "Status": "Acknowledged", "CommandUUID": "ack-cmd-uuid"}
    )
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/connect", content=body
    )
    assert res.status_code == 200

    final = await DeviceCommandRepository(db_session).get_for_organization(
        organization_a.id, command.id
    )
    assert final is not None
    assert final.status == DeviceCommandStatus.COMPLETED


@pytest.mark.asyncio
async def test_connect_status_not_now_leaves_command_unchanged(
    client: AsyncClient,
    db_session: AsyncSession,
    organization_a: Organization,
    org_a_admin_user: User,
) -> None:

    device = await _linked_enrolled_device(db_session, organization_a, udid="NOTNOW-UDID-1")
    command_service = _command_service(db_session)
    command = await command_service.create_command(
        organization_a.id,
        device.id,
        command_type=DeviceCommandType.LOCK,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()
    command = await command_service.process_dispatch_job(organization_a.id, command.id)
    await DeviceCommandRepository(db_session).update_for_organization(
        command, status=DeviceCommandStatus.SENT, provider_command_id="notnow-cmd-uuid"
    )
    await db_session.commit()

    body = plistlib.dumps(
        {"UDID": "NOTNOW-UDID-1", "Status": "NotNow", "CommandUUID": "notnow-cmd-uuid"}
    )
    res = await client.post(
        f"/api/v1/mdm/apple/{organization_a.id}/connect", content=body
    )
    assert res.status_code == 200

    unchanged = await DeviceCommandRepository(db_session).get_for_organization(
        organization_a.id, command.id
    )
    assert unchanged is not None
    assert unchanged.status == DeviceCommandStatus.SENT


@pytest.mark.asyncio
async def test_apple_health_requires_platform_permission(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get("/api/v1/mdm/apple/health", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_apple_health_reports_unconfigured_by_default(
    client: AsyncClient, super_admin_user: User
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    res = await client.get("/api/v1/mdm/apple/health", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    assert res.json()["configured"] is False
