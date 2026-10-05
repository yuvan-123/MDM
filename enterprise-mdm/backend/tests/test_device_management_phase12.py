"""Phase 12 — production device management operations: the UNENROLL
command, provider-aware capability discovery/gating, and device
management-state/provider visibility on GET /devices. See
app/commands/service.py, app/platform/*/provider.py, and
docs/architecture/phase-12.md.

Does not re-test Phase 6-11 command/enrollment mechanics (idempotency,
wipe safeguards, tenant isolation, dispatch) — those are covered by
tests/test_commands.py, tests/test_command_wipe_safeguards.py,
tests/test_command_tenant_isolation.py, etc., and unaffected by this
phase except where explicitly noted.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.commands.service import CommandService
from app.core.config import Settings
from app.core.errors import AppError
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from tests.conftest import _login, dispatch_pending_command
from tests.test_commands import _enroll_device


def _register_payload(token: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "token": token,
        "display_name": "Phase 12 Test Device",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
    }
    payload.update(overrides)
    return payload


def _make_command_service(db_session: AsyncSession, settings: Settings) -> CommandService:
    return CommandService(
        command_repo=DeviceCommandRepository(db_session),
        device_repo=DeviceRepository(db_session),
        provider_registry=PlatformProviderRegistry(settings=settings),
        audit_repo=AuditRepository(db_session),
        job_service=JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db_session),
    )


# ---------------------------------------------------------------------------
# Capability gate — rejected BEFORE a command row is created
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_apns_key_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


@pytest.fixture
def configured_apple_settings(tmp_path: Path, fake_apns_key_pem: str) -> Settings:
    key_path = tmp_path / "apns_key.p8"
    key_path.write_text(fake_apns_key_pem)
    return Settings(
        platform_provider_mode="real",
        apple_apns_key_path=str(key_path),
        apple_apns_key_id="FAKEKEYID1",
        apple_apns_team_id="FAKETEAMID1",
        apple_apns_topic="com.apple.mgmt.External.fake-topic-uuid",
    )


@pytest.mark.asyncio
async def test_unsupported_command_type_rejected_before_row_created(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
    configured_apple_settings: Settings,
) -> None:
    """REBOOT is not in AppleManagementProvider's supported_capabilities —
    a fully-configured (but mock-transport-less, since no network call
    should ever happen for this rejection) real Apple provider must reject
    it synchronously, at create_command time, with no DeviceCommand row
    persisted — not queued for a background job to discover and fail."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, platform="IOS"),
    )
    assert reg_res.status_code == 201, reg_res.text
    device_id = uuid.UUID(reg_res.json()["device_id"])

    service = _make_command_service(db_session, configured_apple_settings)
    with pytest.raises(AppError) as exc_info:
        await service.create_command(
            organization_a.id,
            device_id,
            command_type=DeviceCommandType.REBOOT,
            payload=None,
            idempotency_key=None,
            actor_user_id=org_a_admin_user.id,
        )
    assert exc_info.value.code == "COMMAND_TYPE_NOT_SUPPORTED"

    commands, total = await service.list_commands(organization_a.id, device_id)
    assert total == 0


@pytest.mark.asyncio
async def test_unenroll_rejected_for_apple_before_row_created(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
    configured_apple_settings: Settings,
) -> None:
    """UNENROLL specifically — Apple has no supported real mechanism (see
    app/platform/apple/provider.py), documented rather than guessed."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, platform="IOS"),
    )
    device_id = uuid.UUID(reg_res.json()["device_id"])

    service = _make_command_service(db_session, configured_apple_settings)
    with pytest.raises(AppError) as exc_info:
        await service.create_command(
            organization_a.id,
            device_id,
            command_type=DeviceCommandType.UNENROLL,
            payload=None,
            idempotency_key=None,
            actor_user_id=org_a_admin_user.id,
        )
    assert exc_info.value.code == "COMMAND_TYPE_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_provider_unavailable_still_creates_pending_row(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """The opposite case, preserved: an UNAVAILABLE provider (missing
    credentials, real mode) must still produce an audited PENDING/FAILED
    row — the capability-gate change must never swallow this into a bare
    pre-row-creation rejection too. See
    tests/test_command_platform_provider_unavailable.py for the original
    Phase 6 version of this guarantee against a different command type."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    service = _make_command_service(db_session, Settings(platform_provider_mode="real"))
    command = await service.create_command(
        organization_a.id,
        uuid.UUID(device_id),
        command_type=DeviceCommandType.UNENROLL,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    assert command.status == DeviceCommandStatus.PENDING


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_endpoint_lists_all_types_for_mock(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.get(
        f"/api/v1/devices/{device_id}/commands/capabilities",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["provider_name"] == "mock"
    assert body["provider_available"] is True
    # Phase 14 added INSTALL_APPLICATION/UNINSTALL_APPLICATION to every
    # provider's command-type/capability set, mock included.
    assert set(body["supported_command_types"]) == {
        "LOCK",
        "REBOOT",
        "WIPE",
        "REFRESH_INFO",
        "POLICY_SYNC",
        "UNENROLL",
        "INSTALL_APPLICATION",
        "UNINSTALL_APPLICATION",
    }


# ---------------------------------------------------------------------------
# UNENROLL happy path (mock) — safeguards, dispatch, and cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unenroll_missing_confirm_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/unenroll",
        headers={"X-CSRF-Token": csrf},
        json={"idempotency_key": "no-confirm-key"},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_unenroll_missing_idempotency_key_rejected(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/unenroll",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_unenroll_completes_and_cleans_up_provider_link(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, provider_device_id="unenroll-target-1"),
    )
    assert reg_res.status_code == 201, reg_res.text
    device_id = reg_res.json()["device_id"]
    device_secret = reg_res.json()["device_secret"]

    link_repo = ProviderDeviceLinkRepository(db_session)
    assert (
        await link_repo.get_device_id(organization_a.id, "android", "unenroll-target-1")
        is not None
    )

    unenroll_res = await client.post(
        f"/api/v1/devices/{device_id}/commands/unenroll",
        headers={"X-CSRF-Token": csrf},
        json={"confirm": True, "idempotency_key": "unenroll-key-1"},
    )
    assert unenroll_res.status_code == 201, unenroll_res.text

    dispatched = await dispatch_pending_command(
        db_session, organization_a.id, unenroll_res.json()["id"]
    )
    assert dispatched.status == DeviceCommandStatus.COMPLETED

    # Link removed.
    assert (
        await link_repo.get_device_id(organization_a.id, "android", "unenroll-target-1") is None
    )

    # Heartbeat credential revoked, same as wipe.
    heartbeat_res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert heartbeat_res.status_code == 401

    device_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert device_res.json()["enrollment_status"] == "REVOKED"
    assert device_res.json()["management_state"] == "UNMANAGED"


@pytest.mark.asyncio
async def test_help_desk_cannot_unenroll(
    client: AsyncClient, org_a_admin_user: User, org_a_member_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, admin_csrf)

    member_csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/unenroll",
        headers={"X-CSRF-Token": member_csrf},
        json={"confirm": True, "idempotency_key": "help-desk-denied"},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Device management-state / provider visibility on GET /devices
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_list_shows_management_state_and_provider_filter(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False, "max_uses": 2},
    )
    raw_token = token_res.json()["token"]

    managed_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(
            raw_token, display_name="Managed Device", provider_device_id="list-managed-1"
        ),
    )
    assert managed_res.status_code == 201, managed_res.text
    unmanaged_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, display_name="Unmanaged Device"),
    )
    assert unmanaged_res.status_code == 201, unmanaged_res.text

    managed_body = managed_res.json()["device"]
    assert managed_body["management_state"] == "MANAGED"
    assert managed_body["provider_name"] == "android"
    assert managed_body["provider_device_id"] == "list-managed-1"

    unmanaged_body = unmanaged_res.json()["device"]
    assert unmanaged_body["management_state"] == "UNMANAGED"
    assert unmanaged_body["provider_name"] is None

    filtered = await client.get(
        "/api/v1/devices?provider_name=android",
        headers={"X-CSRF-Token": csrf},
    )
    assert filtered.status_code == 200, filtered.text
    filtered_ids = {d["id"] for d in filtered.json()["items"]}
    assert managed_res.json()["device_id"] in filtered_ids
    assert unmanaged_res.json()["device_id"] not in filtered_ids
    assert all(d["management_state"] == "MANAGED" for d in filtered.json()["items"])
