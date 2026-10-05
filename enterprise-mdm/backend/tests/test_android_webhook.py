"""Android Pub/Sub push webhook (Phase 9) — authentication, envelope
parsing, and cross-org device resolution. See
app/platform/android/webhook.py and app/platform/android/router.py."""

from __future__ import annotations

import base64
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.devices.enums import DevicePlatform, DeviceType, OwnershipType
from app.organizations.models import Organization
from app.platform.android.webhook import (
    decode_pubsub_envelope,
    extract_provider_device_id,
    map_notification_to_event_type,
    verify_push_token,
)
from app.provider_events.models import ProviderEventProcessingStatus
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.repositories.provider_event_repository import ProviderEventRepository
from app.users.models import User
from tests.conftest import _login

# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


def test_verify_push_token_rejects_mismatch() -> None:
    assert verify_push_token("secret", "wrong") is False


def test_verify_push_token_rejects_unconfigured() -> None:
    assert verify_push_token(None, "anything") is False


def test_verify_push_token_accepts_match() -> None:
    assert verify_push_token("secret", "secret") is True


def test_decode_pubsub_envelope_extracts_json_payload() -> None:
    raw = json.dumps({"notificationType": "STATUS_REPORT", "device": "enterprises/e/devices/d1"})
    envelope = {"message": {"data": base64.b64encode(raw.encode()).decode(), "messageId": "1"}}
    payload = decode_pubsub_envelope(envelope)
    assert payload["notificationType"] == "STATUS_REPORT"


def test_decode_pubsub_envelope_rejects_missing_message() -> None:
    with pytest.raises(ValueError):
        decode_pubsub_envelope({})


def test_extract_provider_device_id_strips_resource_prefix() -> None:
    assert extract_provider_device_id("enterprises/LC00abc/devices/dev123") == "dev123"


def test_map_notification_type_known_and_unknown() -> None:
    assert map_notification_to_event_type("STATUS_REPORT") == "DEVICE_CHECK_IN"
    assert map_notification_to_event_type("SOMETHING_NEW") == "SOMETHING_NEW"


# ---------------------------------------------------------------------------
# Router integration tests
# ---------------------------------------------------------------------------


def _envelope_for(notification: dict[str, object], message_id: str = "msg-1") -> dict[str, object]:
    raw = json.dumps(notification)
    return {
        "message": {"data": base64.b64encode(raw.encode()).decode(), "messageId": message_id},
        "subscription": "projects/p/subscriptions/s",
    }


@pytest.mark.asyncio
async def test_pubsub_push_rejects_missing_token(client: AsyncClient) -> None:
    res = await client.post(
        "/api/v1/webhooks/android/pubsub", json=_envelope_for({"notificationType": "STATUS_REPORT"})
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_pubsub_push_rejects_wrong_token(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=wrong-token",
            json=_envelope_for({"notificationType": "STATUS_REPORT"}),
        )
        assert res.status_code == 401
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pubsub_push_rejects_malformed_envelope(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=correct-token", json={"not": "an envelope"}
        )
        assert res.status_code == 400
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pubsub_push_accepted_and_resolves_linked_device(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    db_session: AsyncSession,
    organization_a: Organization,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    get_settings.cache_clear()
    try:
        device = await DeviceRepository(db_session).create_for_organization(
            organization_a.id,
            display_name="Webhook Test Device",
            platform=DevicePlatform.ANDROID,
            device_type=DeviceType.PHONE,
            ownership_type=OwnershipType.CORPORATE,
        )
        await ProviderDeviceLinkRepository(db_session).create(
            organization_a.id,
            provider_name="android",
            provider_device_id="dev-webhook-1",
            device_id=device.id,
        )
        await db_session.commit()

        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=correct-token",
            json=_envelope_for(
                {
                    "notificationType": "STATUS_REPORT",
                    "device": "enterprises/e/devices/dev-webhook-1",
                },
                message_id="msg-resolved",
            ),
        )
        assert res.status_code == 200, res.text

        event = await ProviderEventRepository(db_session).get_by_provider_event_id(
            "android", "msg-resolved"
        )
        assert event is not None
        assert event.organization_id == organization_a.id
        assert event.device_id == device.id
        assert event.event_type == "DEVICE_CHECK_IN"
        assert event.processing_status == ProviderEventProcessingStatus.PROCESSED
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_pubsub_push_unresolved_device_still_recorded(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, db_session: AsyncSession
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", "correct-token")
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/webhooks/android/pubsub?token=correct-token",
            json=_envelope_for(
                {
                    "notificationType": "STATUS_REPORT",
                    "device": f"enterprises/e/devices/{uuid.uuid4().hex}",
                },
                message_id="msg-unresolved",
            ),
        )
        assert res.status_code == 200

        event = await ProviderEventRepository(db_session).get_by_provider_event_id(
            "android", "msg-unresolved"
        )
        assert event is not None
        assert event.organization_id is None
        assert event.device_id is None
    finally:
        monkeypatch.delenv("ANDROID_PUBSUB_VERIFICATION_TOKEN", raising=False)
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_android_health_requires_platform_permission(
    client: AsyncClient, org_a_admin_user: User
) -> None:
    """org_a_admin_user has ORGANIZATION_ADMIN scoped to organization_a
    only — a platform-level permission check must not be satisfiable by an
    org-scoped role grant."""
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        "/api/v1/webhooks/android/health", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_android_health_reports_unconfigured_by_default(
    client: AsyncClient, super_admin_user: User
) -> None:
    csrf = await _login(client, super_admin_user.email, "SuperSecret123!")
    res = await client.get(
        "/api/v1/webhooks/android/health", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200
    assert res.json()["configured"] is False
