"""Phase 11 — automatic ProviderDeviceLink creation at device registration,
duplicate-enrollment rejection, and provider-link cleanup on enrollment
revocation. See app/enrollment/service.py and docs/architecture/phase-11.md.

Does not re-test Phase 5's token lifecycle (expiry/reuse/revocation) —
that's covered by tests/test_enrollment.py and unaffected by this phase.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from tests.conftest import _login


def _register_payload(token: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "token": token,
        "display_name": "Field Rep Phone",
        "platform": "ANDROID",
        "device_type": "PHONE",
        "ownership_type": "CORPORATE",
    }
    payload.update(overrides)
    return payload


async def _create_auto_token(client: AsyncClient, csrf: str) -> str:
    res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False, "max_uses": 10},
    )
    assert res.status_code == 201, res.text
    return str(res.json()["token"])


@pytest.mark.asyncio
async def test_register_with_provider_device_id_creates_link(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)

    res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, provider_device_id="android-dev-001"),
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["provider_link_created"] is True

    link = await ProviderDeviceLinkRepository(db_session).get_device_id(
        organization_a.id, "android", "android-dev-001"
    )
    assert link == uuid.UUID(body["device_id"])


@pytest.mark.asyncio
async def test_register_without_provider_device_id_creates_no_link(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)

    res = await client.post("/api/v1/enrollment/register", json=_register_payload(raw_token))
    assert res.status_code == 201, res.text
    assert res.json()["provider_link_created"] is False


@pytest.mark.asyncio
async def test_duplicate_provider_device_id_in_same_org_rejected(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)

    first = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, provider_device_id="dup-device", display_name="One"),
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, provider_device_id="dup-device", display_name="Two"),
    )
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "DEVICE_ALREADY_ENROLLED"

    # No duplicate Device was created for the rejected attempt — exactly
    # one device resolves for this provider_device_id, and it's the first.
    org_id = uuid.UUID(first.json()["device"]["organization_id"])
    resolved_device_id = await ProviderDeviceLinkRepository(db_session).get_device_id(
        org_id, "android", "dup-device"
    )
    assert resolved_device_id == uuid.UUID(first.json()["device_id"])


@pytest.mark.asyncio
async def test_same_provider_device_id_allowed_across_different_organizations(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
) -> None:
    csrf_a = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_a = await _create_auto_token(client, csrf_a)
    res_a = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(token_a, provider_device_id="shared-id"),
    )
    assert res_a.status_code == 201, res_a.text

    csrf_b = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    token_b = await _create_auto_token(client, csrf_b)
    res_b = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(token_b, provider_device_id="shared-id"),
    )
    assert res_b.status_code == 201, res_b.text
    assert res_a.json()["device_id"] != res_b.json()["device_id"]


@pytest.mark.asyncio
async def test_apple_push_token_rejected_for_android_platform(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)

    res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, apple_push_token="some-push-token"),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "APPLE_PUSH_TOKEN_NOT_APPLICABLE"


@pytest.mark.asyncio
async def test_provider_device_id_rejected_for_other_platform(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)

    res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(
            raw_token, platform="OTHER", provider_device_id="whatever"
        ),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "PROVIDER_LINKING_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_apple_registration_with_udid_and_push_token_creates_apple_link(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)

    res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(
            raw_token,
            platform="IOS",
            provider_device_id="00008030-000000000000000A",
            apple_push_token="fake-apns-token",
        ),
    )
    assert res.status_code == 201, res.text
    assert res.json()["provider_link_created"] is True

    link_repo = ProviderDeviceLinkRepository(db_session)
    device_id = await link_repo.get_device_id(
        organization_a.id, "apple", "00008030-000000000000000A"
    )
    assert device_id == uuid.UUID(res.json()["device_id"])
    push_token = await link_repo.get_apple_push_token(organization_a.id, device_id)
    assert push_token == "fake-apns-token"


@pytest.mark.asyncio
async def test_enrollment_detail_shows_provider_link_info(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, provider_device_id="detail-device"),
    )
    device_id = reg_res.json()["device_id"]

    detail_res = await client.get(
        f"/api/v1/devices/{device_id}/enrollment", headers={"X-CSRF-Token": csrf}
    )
    assert detail_res.status_code == 200, detail_res.text
    assert detail_res.json()["provider_name"] == "android"
    assert detail_res.json()["provider_device_id"] == "detail-device"


@pytest.mark.asyncio
async def test_revoke_enrollment_deletes_provider_link_but_keeps_device(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    raw_token = await _create_auto_token(client, csrf)
    reg_res = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, provider_device_id="revoke-device"),
    )
    device_id = reg_res.json()["device_id"]

    revoke_res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/revoke", headers={"X-CSRF-Token": csrf}
    )
    assert revoke_res.status_code == 200, revoke_res.text

    link_repo = ProviderDeviceLinkRepository(db_session)
    resolved = await link_repo.get_device_id(organization_a.id, "android", "revoke-device")
    assert resolved is None

    # Device row itself, and its full history, is preserved.
    device_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert device_res.status_code == 200
    assert device_res.json()["id"] == device_id
