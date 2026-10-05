"""Phase 11 — real Android enrollment-token provisioning
(`enroll_device` / `enrollmentTokens.create`) and the webhook auto-linking
it enables: a never-before-seen device checking in resolves back to the
organization that minted its enrollment token via `enrollmentTokenName`,
with no admin database write required. See
app/platform/android/{client,provider}.py, app/enrollment/service.py, and
docs/architecture/phase-11.md.

Shape-correctness tests only, mirroring tests/test_android_provider.py's
own scope note: never live-verified against a real Android Enterprise
account — this environment has no real credentials.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.platform.android.client as android_client_module
from app.core.config import Settings, get_settings
from app.enrollment.models import EnrollmentTokenStatus
from app.organizations.models import Organization
from app.platform.android.client import AndroidApiClient
from app.platform.android.provider import AndroidManagementProvider
from app.platform.apple.provider import AppleManagementProvider
from app.platform.base.errors import PlatformProviderUnavailableError
from app.platform.mock.provider import MockManagementProvider
from app.provider_events.models import ProviderDeviceLinkSource
from app.repositories.enrollment_token_repository import EnrollmentTokenRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from tests.conftest import _login

# ---------------------------------------------------------------------------
# AndroidApiClient / AndroidManagementProvider unit tests (mock transport)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    android_client_module._token_cache.clear()


@pytest.fixture(scope="module")
def fake_private_key_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode()


@pytest.fixture
def configured_settings(fake_private_key_pem: str) -> Settings:
    service_account = json.dumps(
        {"client_email": "test@test.iam.gserviceaccount.com", "private_key": fake_private_key_pem}
    )
    return Settings(
        platform_provider_mode="real",
        android_service_account_json=service_account,
        android_enterprise_id="LC00fakeenterprise",
    )


_RealAsyncClient = httpx.AsyncClient


def _mock_transport(
    handler: Callable[[httpx.Request], httpx.Response], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _factory(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.platform.android.client.httpx.AsyncClient", _factory)


@pytest.mark.asyncio
async def test_create_enrollment_token_sends_correctly_shaped_request(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "name": "enterprises/LC00fakeenterprise/enrollmentTokens/tok123",
                "value": "raw-enrollment-secret",
                "qrCode": '{"android.app.extra.PROVISIONING...": true}',
                "expirationTimestamp": "2026-09-01T00:00:00.000Z",
            },
        )

    _mock_transport(handler, monkeypatch)
    client = AndroidApiClient(
        configured_settings.android_service_account_json, configured_settings.android_enterprise_id
    )
    result = await client.create_enrollment_token(
        policy_name="enterprises/LC00fakeenterprise/policies/p1", duration_seconds=3600
    )

    assert result["value"] == "raw-enrollment-secret"
    request = captured["request"]
    assert request.method == "POST"
    assert "enrollmentTokens" in str(request.url)
    body = json.loads(request.content)
    assert body == {
        "policyName": "enterprises/LC00fakeenterprise/policies/p1",
        "duration": "3600s",
        # Required for provisioning a personally-owned device as a work
        # profile — see app/platform/android/client.py's own docstring.
        "allowPersonalUsage": "PERSONAL_USAGE_ALLOWED",
    }


@pytest.mark.asyncio
async def test_get_device_resource_returns_full_device(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        assert request.method == "GET"
        assert "devices/dev-1" in str(request.url)
        return httpx.Response(
            200,
            json={
                "name": "enterprises/LC00fakeenterprise/devices/dev-1",
                "policyName": "enterprises/LC00fakeenterprise/policies/p1",
                "enrollmentTokenName": "enterprises/LC00fakeenterprise/enrollmentTokens/tok123",
            },
        )

    _mock_transport(handler, monkeypatch)
    client = AndroidApiClient(
        configured_settings.android_service_account_json, configured_settings.android_enterprise_id
    )
    resource = await client.get_device_resource("dev-1")
    assert (
        resource["enrollmentTokenName"]
        == "enterprises/LC00fakeenterprise/enrollmentTokens/tok123"
    )


@pytest.mark.asyncio
async def test_android_provider_enroll_device_maps_response(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        return httpx.Response(
            200,
            json={
                "name": "enterprises/LC00fakeenterprise/enrollmentTokens/tok999",
                "value": "raw-secret-value",
                "expirationTimestamp": "2026-09-01T00:00:00+00:00",
            },
        )

    _mock_transport(handler, monkeypatch)
    provider = AndroidManagementProvider(configured_settings)
    credential = await provider.enroll_device(policy_id="policy-abc", duration_seconds=1800)

    assert credential.value == "raw-secret-value"
    assert credential.reference == "enterprises/LC00fakeenterprise/enrollmentTokens/tok999"
    assert credential.expires_at is not None


@pytest.mark.asyncio
async def test_apple_provider_enroll_device_documents_unavailable() -> None:
    settings = Settings(
        platform_provider_mode="real",
        apple_apns_key_path="/fake/key.p8",
        apple_apns_key_id="KEYID",
        apple_apns_team_id="TEAMID",
        apple_apns_topic="com.example.mdm",
    )
    provider = AppleManagementProvider(settings)
    with pytest.raises(PlatformProviderUnavailableError) as exc_info:
        await provider.enroll_device(policy_id="whatever")
    assert exc_info.value.code == "PLATFORM_PROVIDER_UNAVAILABLE"
    assert "Business Manager" in exc_info.value.message


@pytest.mark.asyncio
async def test_mock_provider_enroll_device_is_deterministic() -> None:
    provider = MockManagementProvider()
    credential = await provider.enroll_device(policy_id="policy-xyz")
    assert credential.value.startswith("mock-enrollment-token-")
    assert credential.reference is not None
    assert "policy-xyz" in credential.reference


# ---------------------------------------------------------------------------
# API-level: create_token(provision_via_provider=True) against the default
# mock provider (platform_provider_mode="mock", this test suite's default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provision_via_provider_requires_allowed_platform(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "provision_via_provider": True},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "PROVIDER_PROVISIONING_REQUIRES_PLATFORM"


@pytest.mark.asyncio
async def test_provision_via_provider_mints_credential_via_mock(
    client: AsyncClient, org_a_admin_user: User, db_session: AsyncSession
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={
            "expires_in_hours": 24,
            "allowed_platform": "ANDROID",
            "provision_via_provider": True,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["provision_via_provider"] is True
    assert body["provider_enrollment_value"] is not None
    assert body["provider_enrollment_value"].startswith("mock-enrollment-token-")
    assert body["provider_enrollment_reference"] is not None

    token = await EnrollmentTokenRepository(db_session).get_for_organization(
        uuid.UUID(body["organization_id"]), uuid.UUID(body["id"])
    )
    assert token is not None
    assert token.provider_enrollment_reference == body["provider_enrollment_reference"]
    # The secret value itself is never persisted anywhere on the row.
    assert "mock-enrollment-token-" not in (token.provider_enrollment_reference or "")


# ---------------------------------------------------------------------------
# End-to-end: a real (mock-transport) Android check-in for a never-before-
# seen device auto-resolves the organization and enrolls it.
# ---------------------------------------------------------------------------


def _envelope_for(notification: dict[str, object], message_id: str) -> dict[str, object]:
    import base64

    raw = json.dumps(notification)
    return {
        "message": {"data": base64.b64encode(raw.encode()).decode(), "messageId": message_id},
        "subscription": "projects/p/subscriptions/s",
    }


@pytest.mark.asyncio
async def test_webhook_auto_enrolls_previously_unseen_device(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    org_a_admin_user: User,
    organization_a: Organization,
    fake_private_key_pem: str,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": False},
    )
    token_id = uuid.UUID(token_res.json()["id"])
    # provider_enrollment_reference is normally set by provision_via_provider
    # against a real/mock provider at token-creation time — set directly
    # here so this test can focus purely on webhook-side resolution.
    reference = "enterprises/LC00fakeenterprise/enrollmentTokens/tok-checkin"
    token_repo = EnrollmentTokenRepository(db_session)
    token = await token_repo.get_for_organization(organization_a.id, token_id)
    assert token is not None
    token.provider_enrollment_reference = reference
    await db_session.commit()

    service_account = json.dumps(
        {"client_email": "test@test.iam.gserviceaccount.com", "private_key": fake_private_key_pem}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        return httpx.Response(
            200,
            json={
                "name": "enterprises/LC00fakeenterprise/devices/never-seen-1",
                "policyName": "enterprises/LC00fakeenterprise/policies/p1",
                "enrollmentTokenName": reference,
            },
        )

    _mock_transport(handler, monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    monkeypatch.setenv("ANDROID_SERVICE_ACCOUNT_JSON", service_account)
    monkeypatch.setenv("ANDROID_ENTERPRISE_ID", "LC00fakeenterprise")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=correct-token",
            json=_envelope_for(
                {
                    "notificationType": "STATUS_REPORT",
                    "device": "enterprises/LC00fakeenterprise/devices/never-seen-1",
                },
                message_id="msg-auto-enroll",
            ),
        )
        assert res.status_code == 200, res.text

        link_repo = ProviderDeviceLinkRepository(db_session)
        device_id = await link_repo.get_device_id(organization_a.id, "android", "never-seen-1")
        assert device_id is not None
        link = await link_repo.get_link_for_device(organization_a.id, device_id, "android")
        assert link is not None
        assert link.link_source == ProviderDeviceLinkSource.PROVIDER_ENROLLED

        refreshed_token = await token_repo.get_for_organization(organization_a.id, token_id)
        assert refreshed_token is not None
        assert refreshed_token.use_count == 1
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        monkeypatch.delenv("ANDROID_SERVICE_ACCOUNT_JSON", raising=False)
        monkeypatch.delenv("ANDROID_ENTERPRISE_ID", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_webhook_auto_enroll_respects_require_approval(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    org_a_admin_user: User,
    organization_a: Organization,
    fake_private_key_pem: str,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24, "require_approval": True},
    )
    token_id = uuid.UUID(token_res.json()["id"])
    reference = "enterprises/LC00fakeenterprise/enrollmentTokens/tok-pending"
    token_repo = EnrollmentTokenRepository(db_session)
    token = await token_repo.get_for_organization(organization_a.id, token_id)
    assert token is not None
    token.provider_enrollment_reference = reference
    await db_session.commit()

    service_account = json.dumps(
        {"client_email": "test@test.iam.gserviceaccount.com", "private_key": fake_private_key_pem}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        return httpx.Response(
            200,
            json={
                "name": "enterprises/LC00fakeenterprise/devices/needs-approval-1",
                "enrollmentTokenName": reference,
            },
        )

    _mock_transport(handler, monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    monkeypatch.setenv("ANDROID_SERVICE_ACCOUNT_JSON", service_account)
    monkeypatch.setenv("ANDROID_ENTERPRISE_ID", "LC00fakeenterprise")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=correct-token",
            json=_envelope_for(
                {
                    "notificationType": "STATUS_REPORT",
                    "device": "enterprises/LC00fakeenterprise/devices/needs-approval-1",
                },
                message_id="msg-pending",
            ),
        )
        assert res.status_code == 200, res.text

        link_repo = ProviderDeviceLinkRepository(db_session)
        device_id = await link_repo.get_device_id(
            organization_a.id, "android", "needs-approval-1"
        )
        assert device_id is not None
        device_res = await client.get(
            f"/api/v1/devices/{device_id}", headers={"X-CSRF-Token": csrf}
        )
        assert device_res.json()["enrollment_status"] == "PENDING"
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        monkeypatch.delenv("ANDROID_SERVICE_ACCOUNT_JSON", raising=False)
        monkeypatch.delenv("ANDROID_ENTERPRISE_ID", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_webhook_auto_enroll_rejects_revoked_token(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    org_a_admin_user: User,
    organization_a: Organization,
    fake_private_key_pem: str,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    token_res = await client.post(
        "/api/v1/enrollment/tokens",
        headers={"X-CSRF-Token": csrf},
        json={"expires_in_hours": 24},
    )
    token_id = uuid.UUID(token_res.json()["id"])
    reference = "enterprises/LC00fakeenterprise/enrollmentTokens/tok-revoked"
    token_repo = EnrollmentTokenRepository(db_session)
    token = await token_repo.get_for_organization(organization_a.id, token_id)
    assert token is not None
    token.provider_enrollment_reference = reference
    token.status = EnrollmentTokenStatus.REVOKED
    await db_session.commit()

    service_account = json.dumps(
        {"client_email": "test@test.iam.gserviceaccount.com", "private_key": fake_private_key_pem}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        return httpx.Response(
            200,
            json={
                "name": "enterprises/LC00fakeenterprise/devices/should-not-enroll",
                "enrollmentTokenName": reference,
            },
        )

    _mock_transport(handler, monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    monkeypatch.setenv("ANDROID_SERVICE_ACCOUNT_JSON", service_account)
    monkeypatch.setenv("ANDROID_ENTERPRISE_ID", "LC00fakeenterprise")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=correct-token",
            json=_envelope_for(
                {
                    "notificationType": "STATUS_REPORT",
                    "device": "enterprises/LC00fakeenterprise/devices/should-not-enroll",
                },
                message_id="msg-revoked-token",
            ),
        )
        assert res.status_code == 200

        link_repo = ProviderDeviceLinkRepository(db_session)
        device_id = await link_repo.get_device_id(
            organization_a.id, "android", "should-not-enroll"
        )
        assert device_id is None
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        monkeypatch.delenv("ANDROID_SERVICE_ACCOUNT_JSON", raising=False)
        monkeypatch.delenv("ANDROID_ENTERPRISE_ID", raising=False)
        get_settings.cache_clear()
