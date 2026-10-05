"""Enrollment token lifecycle + device self-registration (Phase 5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.organizations.models import Organization
from app.repositories.enrollment_token_repository import EnrollmentTokenRepository
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


@pytest.mark.asyncio
async def test_create_enrollment_token(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"label": "Sales team", "expires_in_hours": 24, "max_uses": 5},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert "token" in body and len(body["token"]) > 20
    assert body["status"] == "ACTIVE"
    assert body["use_count"] == 0
    assert body["max_uses"] == 5


@pytest.mark.asyncio
async def test_help_desk_cannot_create_token_without_permission(
    client: AsyncClient, org_a_auditor_user: User
) -> None:
    csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")
    res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_successful_device_registration(
    client: AsyncClient, org_a_admin_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]

    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    assert reg_res.status_code == 201, reg_res.text
    body = reg_res.json()
    assert body["device"]["organization_id"] == str(organization_a.id)
    assert body["device"]["enrollment_status"] == "ENROLLED"
    assert body["enrollment_status"] == "ENROLLED"
    assert len(body["device_secret"]) > 20


@pytest.mark.asyncio
async def test_registration_requires_approval_by_default(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},
    )
    raw_token = token_res.json()["token"]
    assert token_res.json()["require_approval"] is True

    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    assert reg_res.status_code == 201
    assert reg_res.json()["device"]["enrollment_status"] == "PENDING"


@pytest.mark.asyncio
async def test_invalid_token_rejected(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload("not-a-real-token")
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_ENROLLMENT_TOKEN"


@pytest.mark.asyncio
async def test_expired_token_rejected(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    """The minimum `expires_in_hours` is 1, so expiry is exercised by
    directly backdating the persisted row rather than time-travel — this
    still exercises the real lazy-expiry check in
    EnrollmentService.register_device (STEP 2's "have expiration")."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 1},
    )
    token_id = token_res.json()["id"]
    raw_token = token_res.json()["token"]

    token_repo = EnrollmentTokenRepository(db_session)
    org_id = uuid.UUID(token_res.json()["organization_id"])
    token = await token_repo.get_for_organization(org_id, uuid.UUID(token_id))
    assert token is not None
    token.expires_at = datetime.now(tz=UTC) - timedelta(hours=1)
    await db_session.commit()

    res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "INVALID_ENROLLMENT_TOKEN"


@pytest.mark.asyncio
async def test_revoked_token_rejected(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},
    )
    token_id = token_res.json()["id"]
    raw_token = token_res.json()["token"]

    revoke_res = await client.delete(
        f"/api/v1/enrollment/tokens/{token_id}", headers={"X-CSRF-Token": csrf}
    )
    assert revoke_res.status_code == 200
    assert revoke_res.json()["status"] == "REVOKED"

    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    assert reg_res.status_code == 400
    assert reg_res.json()["error"]["code"] == "INVALID_ENROLLMENT_TOKEN"


@pytest.mark.asyncio
async def test_single_use_token_cannot_be_reused(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "max_uses": 1},
    )
    raw_token = token_res.json()["token"]

    first = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/enrollment/register",
        json=_register_payload(raw_token, display_name="Second Device"),
    )
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "INVALID_ENROLLMENT_TOKEN"


@pytest.mark.asyncio
async def test_multi_use_token_allows_reuse_up_to_limit(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "max_uses": 2, "require_approval": False},
    )
    raw_token = token_res.json()["token"]

    first = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token, display_name="Device 1")
    )
    second = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token, display_name="Device 2")
    )
    third = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token, display_name="Device 3")
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert third.status_code == 400


@pytest.mark.asyncio
async def test_platform_restricted_token_rejects_other_platform(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "allowed_platform": "IOS"},
    )
    raw_token = token_res.json()["token"]

    res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token, platform="ANDROID")
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "PLATFORM_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_approve_pending_enrollment(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    device_id = reg_res.json()["device_id"]
    assert reg_res.json()["enrollment_status"] == "PENDING"

    approve_res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/approve", headers={"X-CSRF-Token": csrf}
    )
    assert approve_res.status_code == 200, approve_res.text
    assert approve_res.json()["status"] == "ENROLLED"

    device_res = await client.get(
        f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
    )
    assert device_res.json()["enrollment_status"] == "ENROLLED"
    assert device_res.json()["enrolled_at"] is not None


@pytest.mark.asyncio
async def test_reject_pending_enrollment(client: AsyncClient, org_a_admin_user: User) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    device_id = reg_res.json()["device_id"]

    reject_res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/reject",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "Not an approved device model."},
    )
    assert reject_res.status_code == 200
    assert reject_res.json()["status"] == "REJECTED"
    assert reject_res.json()["rejection_reason"] == "Not an approved device model."


@pytest.mark.asyncio
async def test_cannot_approve_already_enrolled_device(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    device_id = reg_res.json()["device_id"]

    res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/approve", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_ENROLLMENT_TRANSITION"


@pytest.mark.asyncio
async def test_revoke_enrolled_device_clears_credential(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    device_id = reg_res.json()["device_id"]
    device_secret = reg_res.json()["device_secret"]

    revoke_res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/revoke", headers={"X-CSRF-Token": csrf}
    )
    assert revoke_res.status_code == 200
    assert revoke_res.json()["status"] == "REVOKED"

    heartbeat_res = await client.post(
        f"/api/v1/devices/{device_id}/heartbeat",
        headers={"X-Device-Id": device_id, "X-Device-Secret": device_secret},
        json={},
    )
    assert heartbeat_res.status_code == 401
    assert heartbeat_res.json()["error"]["code"] == "DEVICE_CREDENTIAL_INVALID"


@pytest.mark.asyncio
async def test_unauthorized_approve_denied(
    client: AsyncClient, org_a_admin_user: User, org_a_auditor_user: User
) -> None:
    admin_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": admin_csrf},
        json={"expires_in_hours": 24},
    )
    raw_token = token_res.json()["token"]
    reg_res = await client.post(
        "/api/v1/enrollment/register", json=_register_payload(raw_token)
    )
    device_id = reg_res.json()["device_id"]

    auditor_csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")
    res = await client.post(
        f"/api/v1/devices/{device_id}/enrollment/approve", headers={"X-CSRF-Token": auditor_csrf}
    )
    assert res.status_code == 403
