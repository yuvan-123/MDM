"""AppleManagementProvider — shape-correctness tests only (Phase 6,
extended Phase 9 for ApnsApiClient's token caching + error classification).

Asserts the APNs wake-up push (channel 1 of the Apple MDM protocol — see
the provider module's docstring) is built and sent correctly against a
mocked HTTP transport. NOT proof of live-integration: this environment has
no real APNs certificate, no push topic, and no device with a real APNs
push token or MDM PushMagic on record.

Phase 9 moved HTTP/token-caching transport out of
app/platform/apple/provider.py into
app/platform/apple/client.py::ApnsApiClient (see that module's docstring)
— the mock-transport monkeypatch target below moved with it.
`_clear_token_cache` resets that module's process-wide token cache before
every test in this file (keyed by (key_id, team_id), which is identical
across every test here since `fake_apns_key_pem` is module-scoped) so an
earlier test's cached token can never mask a later test's own transport
mock.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import app.platform.apple.client as apple_client_module
from app.core.config import Settings
from app.devices.enums import DevicePlatform
from app.platform.apple.provider import AppleManagementProvider
from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError
from app.platform.base.provider import (
    ProviderCommandRequest,
    ProviderCommandStatus,
    ProviderDeviceRef,
)


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    apple_client_module._token_cache.clear()


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
def configured_settings(tmp_path: Path, fake_apns_key_pem: str) -> Settings:
    key_path = tmp_path / "apns_key.p8"
    key_path.write_text(fake_apns_key_pem)
    return Settings(
        platform_provider_mode="real",
        apple_apns_key_path=str(key_path),
        apple_apns_key_id="FAKEKEYID1",
        apple_apns_team_id="FAKETEAMID1",
        apple_apns_topic="com.apple.mgmt.External.fake-topic-uuid",
    )


_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatch below


def _mock_transport(
    handler: Callable[[httpx.Request], httpx.Response], monkeypatch: pytest.MonkeyPatch
) -> None:
    # See tests/test_android_provider.py::_mock_transport for why this must
    # use _RealAsyncClient rather than calling httpx.AsyncClient again.
    def _factory(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.platform.apple.client.httpx.AsyncClient", _factory)


def test_construction_raises_when_unconfigured() -> None:
    with pytest.raises(PlatformProviderUnavailableError) as exc_info:
        AppleManagementProvider(Settings(platform_provider_mode="real"))
    assert exc_info.value.code == "PLATFORM_PROVIDER_UNAVAILABLE"


def test_supported_capabilities_exclude_reboot_and_policy_sync(
    configured_settings: Settings,
) -> None:
    """Deliberate, researched gap — see the provider module's docstring:
    Apple's published MDM schema has no iOS/iPadOS REBOOT-equivalent, and
    POLICY_SYNC's real mechanism (InstallProfile) wasn't verified in this
    research pass, so it is left unsupported rather than guessed."""
    provider = AppleManagementProvider(configured_settings)
    capabilities = {c.value for c in provider.supported_capabilities}
    assert capabilities == {"LOCK", "WIPE", "REFRESH_INFO"}


@pytest.mark.asyncio
async def test_lock_device_sends_correctly_shaped_apns_push(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["push"] = request
        return httpx.Response(200)

    _mock_transport(handler, monkeypatch)
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
        payload={"message": "Please return to IT"},
    )
    result = await provider.lock_device(request)

    assert result.status == ProviderCommandStatus.SENT
    assert result.provider_command_id is not None

    push = captured["push"]
    assert push.method == "POST"
    assert "/3/device/fake-push-token" in str(push.url)
    assert push.headers["apns-topic"] == "com.apple.mgmt.External.fake-topic-uuid"
    assert push.headers["apns-push-type"] == "mdm"
    assert push.headers["authorization"].startswith("bearer ")


@pytest.mark.asyncio
async def test_wipe_device_dispatches(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_transport(lambda request: httpx.Response(200), monkeypatch)
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
    )
    result = await provider.wipe_device(request)
    assert result.status == ProviderCommandStatus.SENT
    assert result.result is not None
    assert result.result["request_type"] == "EraseDevice"


@pytest.mark.asyncio
async def test_reboot_is_rejected_as_unsupported(configured_settings: Settings) -> None:
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
    )
    with pytest.raises(PlatformCommandRejectedError):
        await provider.restart_device(request)


@pytest.mark.asyncio
async def test_missing_push_token_rejected(configured_settings: Settings) -> None:
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(device_id=uuid.uuid4(), platform=DevicePlatform.IOS)
    )
    with pytest.raises(PlatformCommandRejectedError):
        await provider.lock_device(request)


@pytest.mark.asyncio
async def test_apns_error_response_raises_command_rejected(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_transport(
        lambda request: httpx.Response(403, json={"reason": "BadDeviceToken"}), monkeypatch
    )
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
    )
    with pytest.raises(PlatformCommandRejectedError):
        await provider.lock_device(request)


# ---------------------------------------------------------------------------
# Phase 9: ApnsApiClient — token caching and error classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_token_is_cached_across_calls(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Apple's own guidance is to reuse a provider token across requests,
    not mint one per push — assert a second dispatch doesn't re-read the
    signing key file (the only observable signal `ApnsApiClient` exposes
    for 'did it mint a new token')."""
    read_calls = 0
    original_open = open

    def counting_open(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        nonlocal read_calls
        read_calls += 1
        return original_open(*args, **kwargs)

    # `_read_private_key`'s bare `open(...)` call resolves through its own
    # module's globals before falling back to builtins — setting it as a
    # module attribute here shadows the builtin for that module only,
    # without touching every other file read in the test process.
    monkeypatch.setattr(apple_client_module, "open", counting_open, raising=False)

    _mock_transport(lambda request: httpx.Response(200), monkeypatch)
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
    )

    await provider.lock_device(request)
    await provider.wipe_device(request)

    assert read_calls == 1, "second dispatch must reuse the cached provider token"


@pytest.mark.asyncio
async def test_rate_limit_response_is_classified_as_transient(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_transport(lambda request: httpx.Response(429), monkeypatch)
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
    )
    with pytest.raises(PlatformProviderUnavailableError):
        await provider.lock_device(request)


@pytest.mark.asyncio
async def test_server_error_response_is_classified_as_transient(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_transport(lambda request: httpx.Response(503), monkeypatch)
    provider = AppleManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.IOS,
            provider_device_id="fake-push-token",
        ),
    )
    with pytest.raises(PlatformProviderUnavailableError):
        await provider.lock_device(request)


@pytest.mark.asyncio
async def test_health_check_reports_ok_with_valid_credentials(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.platform.apple.client import ApnsApiClient

    _mock_transport(lambda request: httpx.Response(200), monkeypatch)
    assert configured_settings.apple_apns_key_path is not None
    assert configured_settings.apple_apns_key_id is not None
    assert configured_settings.apple_apns_team_id is not None
    assert configured_settings.apple_apns_topic is not None
    client = ApnsApiClient(
        configured_settings.apple_apns_key_path,
        configured_settings.apple_apns_key_id,
        configured_settings.apple_apns_team_id,
        configured_settings.apple_apns_topic,
    )
    ok, error = await client.health_check()
    assert ok is True
    assert error is None


@pytest.mark.asyncio
async def test_health_check_reports_error_with_missing_key_file() -> None:
    from app.platform.apple.client import ApnsApiClient

    client = ApnsApiClient(
        "/no/such/file.p8", "FAKEKEYID1", "FAKETEAMID1", "com.apple.mgmt.External.fake"
    )
    ok, error = await client.health_check()
    assert ok is False
    assert error is not None
