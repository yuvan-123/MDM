"""AndroidManagementProvider — shape-correctness tests only (Phase 6,
extended Phase 9 for AndroidApiClient's token caching + error
classification).

These assert the request/response mapping is structurally correct against
a mocked HTTP transport (httpx.MockTransport, no new dependency needed).
They are NOT proof the implementation works against a real Android
Enterprise account — this environment has no real service-account
credentials and no Docker to verify against Postgres either. See
docs/architecture/device-commands.md and
docs/android/enterprise-capabilities.md for the honest scope of this
provider.

Phase 9 moved HTTP transport out of app/platform/android/provider.py into
app/platform/android/client.py::AndroidApiClient (see that module's
docstring) — the mock-transport monkeypatch target below moved with it.
`AndroidApiClient` also now caches OAuth2 tokens in *process-wide* module
state (keyed by service-account client_email, not per-instance — see that
module's docstring for why), so `_clear_token_cache` below resets it before
every test in this file; without it, a token cached by an earlier test
would silently make a later test's own token-endpoint mock unreachable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import app.platform.android.client as android_client_module
from app.core.config import Settings
from app.devices.enums import DevicePlatform
from app.platform.android.provider import AndroidManagementProvider
from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError
from app.platform.base.provider import (
    ProviderCommandRequest,
    ProviderCommandStatus,
    ProviderDeviceRef,
)


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


_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatch below


def _mock_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # NOTE: `app.platform.android.client.httpx` IS the real `httpx` module
    # object (imported as `import httpx`, not `from httpx import AsyncClient`)
    # — patching `.AsyncClient` on it patches the module attribute globally.
    # The factory below must therefore call `_RealAsyncClient` (captured
    # above, before this patch ever runs), never `httpx.AsyncClient` again,
    # or it recurses into itself infinitely.
    def _factory(**kwargs: object) -> httpx.AsyncClient:
        return _RealAsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.platform.android.client.httpx.AsyncClient", _factory)


def _token_and_command_handler(
    command_json: dict[str, object], command_status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            assert request.method == "POST"
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        return httpx.Response(command_status, json=command_json)

    return handler


def test_construction_raises_when_unconfigured() -> None:
    with pytest.raises(PlatformProviderUnavailableError) as exc_info:
        AndroidManagementProvider(Settings(platform_provider_mode="real"))
    assert exc_info.value.code == "PLATFORM_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_lock_device_sends_correctly_shaped_issue_command(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        captured["issue_command"] = request
        return httpx.Response(
            200, json={"name": "enterprises/LC00fakeenterprise/operations/abc123"}
        )

    _mock_transport(handler, monkeypatch)
    provider = AndroidManagementProvider(configured_settings)

    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(),
            platform=DevicePlatform.ANDROID,
            provider_device_id="fake-device-123",
        ),
    )
    result = await provider.lock_device(request)

    assert result.status == ProviderCommandStatus.SENT
    assert result.provider_command_id == "enterprises/LC00fakeenterprise/operations/abc123"

    issued = captured["issue_command"]
    assert issued.method == "POST"
    assert "fake-device-123:issueCommand" in str(issued.url)
    assert issued.headers["authorization"] == "Bearer fake-access-token"
    body = json.loads(issued.content)
    assert body == {"type": "LOCK"}


@pytest.mark.asyncio
async def test_wipe_device_includes_wipe_params(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(200, json={"access_token": "fake-access-token"})
        captured["issue_command"] = request
        return httpx.Response(200, json={"name": "operations/wipe-1"})

    _mock_transport(handler, monkeypatch)
    provider = AndroidManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(), platform=DevicePlatform.ANDROID, provider_device_id="dev-1"
        ),
    )
    result = await provider.wipe_device(request)
    assert result.status == ProviderCommandStatus.SENT
    body = json.loads(captured["issue_command"].content)
    assert body["type"] == "WIPE"
    assert "wipeParams" in body


@pytest.mark.asyncio
async def test_missing_provider_device_id_rejected(configured_settings: Settings) -> None:
    provider = AndroidManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(device_id=uuid.uuid4(), platform=DevicePlatform.ANDROID)
    )
    with pytest.raises(PlatformCommandRejectedError):
        await provider.lock_device(request)


@pytest.mark.asyncio
async def test_provider_error_response_raises_command_rejected(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_transport(_token_and_command_handler({"error": "bad request"}, 400), monkeypatch)
    provider = AndroidManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(), platform=DevicePlatform.ANDROID, provider_device_id="dev-1"
        ),
    )
    with pytest.raises(PlatformCommandRejectedError):
        await provider.restart_device(request)


def test_supported_capabilities_include_all_eight(configured_settings: Settings) -> None:
    # LOCK/REBOOT/WIPE/REFRESH_INFO/POLICY_SYNC (Phase 6) + UNENROLL
    # (Phase 12) + APPLICATION_INSTALL/APPLICATION_REMOVE (Phase 14).
    provider = AndroidManagementProvider(configured_settings)
    assert len(provider.supported_capabilities) == 8


# ---------------------------------------------------------------------------
# Phase 9: AndroidApiClient — token caching and error classification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_token_is_cached_across_calls(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of moving auth into AndroidApiClient with a
    process-wide cache — a second dispatch within the token's lifetime
    must not mint a second token."""
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if str(request.url).startswith("https://oauth2.googleapis.com/token"):
            token_requests += 1
            return httpx.Response(200, json={"access_token": "cached-token", "expires_in": 3600})
        return httpx.Response(200, json={"name": "operations/1"})

    _mock_transport(handler, monkeypatch)
    provider = AndroidManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(), platform=DevicePlatform.ANDROID, provider_device_id="dev-1"
        ),
    )

    await provider.lock_device(request)
    await provider.restart_device(request)

    assert token_requests == 1, "second dispatch must reuse the cached token, not re-mint one"


@pytest.mark.asyncio
async def test_rate_limit_response_is_classified_as_transient(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 429/5xx from Google is retryable — must raise
    PlatformProviderUnavailableError (which app/jobs/retry.py::is_transient
    treats as retryable), never PlatformCommandRejectedError (permanent)."""
    _mock_transport(_token_and_command_handler({"error": "rate limited"}, 429), monkeypatch)
    provider = AndroidManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(), platform=DevicePlatform.ANDROID, provider_device_id="dev-1"
        ),
    )
    with pytest.raises(PlatformProviderUnavailableError):
        await provider.lock_device(request)


@pytest.mark.asyncio
async def test_server_error_response_is_classified_as_transient(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mock_transport(_token_and_command_handler({"error": "internal"}, 503), monkeypatch)
    provider = AndroidManagementProvider(configured_settings)
    request = ProviderCommandRequest(
        device=ProviderDeviceRef(
            device_id=uuid.uuid4(), platform=DevicePlatform.ANDROID, provider_device_id="dev-1"
        ),
    )
    with pytest.raises(PlatformProviderUnavailableError):
        await provider.lock_device(request)


@pytest.mark.asyncio
async def test_health_check_reports_ok_with_valid_credentials(
    configured_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.platform.android.client import AndroidApiClient

    _mock_transport(_token_and_command_handler({}), monkeypatch)
    assert configured_settings.android_service_account_json is not None
    assert configured_settings.android_enterprise_id is not None
    client = AndroidApiClient(
        configured_settings.android_service_account_json, configured_settings.android_enterprise_id
    )
    ok, error = await client.health_check()
    assert ok is True
    assert error is None


@pytest.mark.asyncio
async def test_health_check_reports_error_with_malformed_credentials() -> None:
    from app.platform.android.client import AndroidApiClient

    client = AndroidApiClient("not valid json", "some-enterprise")
    ok, error = await client.health_check()
    assert ok is False
    assert error is not None
