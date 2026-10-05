"""APNs transport client (Phase 9).

Separates HTTP/2 transport and provider-token caching from
app/platform/apple/provider.py's domain-verb mapping (LOCK ->
DeviceLock, etc.), mirroring app/platform/android/client.py's split.

## Token caching
Apple's own APNs Provider API documentation recommends reusing one
provider authentication token for repeated requests rather than minting a
fresh one per push — Apple explicitly rate-limits token generation
(historically documented as roughly once per 20 minutes) separately from
push-send rate limits. `AppleManagementProvider` is constructed fresh per
dispatch (same `PlatformProviderRegistry.get_provider` design as Android —
see that module's docstring), so this cache is process-wide module state,
not instance state, mirroring app/platform/android/client.py's identical
reasoning. Keyed by `(key_id, team_id)`, the pair that actually determines
a token's signing identity.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import jwt

from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError

_APNS_BASE = "https://api.push.apple.com"
_REQUEST_TIMEOUT_SECONDS = 10.0
# Apple's own guidance: don't regenerate a provider token more often than
# roughly every 20 minutes. Refreshing at 15 stays safely under that with
# margin, mirroring the Android client's "refresh before actual expiry"
# shape even though APNs tokens don't have a fixed enforced expiry.
_TOKEN_MAX_AGE_SECONDS = 15 * 60

_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_token_cache_lock = asyncio.Lock()


def _classify_http_error(operation: str, response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise PlatformProviderUnavailableError(
            "apple", f"{operation} transiently failed: HTTP {response.status_code}"
        )
    raise PlatformCommandRejectedError(
        "apple", f"{operation} failed: HTTP {response.status_code} {response.text[:200]}"
    )


class ApnsApiClient:
    def __init__(
        self, apns_key_path: str, apns_key_id: str, apns_team_id: str, apns_topic: str
    ) -> None:
        self._apns_key_path = apns_key_path
        self._apns_key_id = apns_key_id
        self._apns_team_id = apns_team_id
        self._apns_topic = apns_topic

    def _read_private_key(self) -> str:
        try:
            with open(self._apns_key_path, encoding="utf-8") as key_file:
                return key_file.read()
        except OSError as exc:
            raise PlatformProviderUnavailableError(
                "apple", f"Could not read APNs signing key: {exc}"
            ) from exc

    async def _get_provider_token(self) -> str:
        cache_key = (self._apns_key_id, self._apns_team_id)
        async with _token_cache_lock:
            cached = _token_cache.get(cache_key)
            now = time.time()
            if cached is not None and now - cached[1] < _TOKEN_MAX_AGE_SECONDS:
                return cached[0]

            private_key = self._read_private_key()
            token = jwt.encode(
                {"iss": self._apns_team_id, "iat": int(now)},
                private_key,
                algorithm="ES256",
                headers={"kid": self._apns_key_id},
            )
            _token_cache[cache_key] = (token, now)
            return token

    async def send_wakeup_push(self, push_token: str) -> None:
        """Channel 1 only — a successful send means the device was
        NOTIFIED, never that it fetched, applied, or received the actual
        command (that happens over channel 2 — see
        app/platform/apple/provider.py's module docstring)."""
        token = await self._get_provider_token()
        url = f"{_APNS_BASE}/3/device/{push_token}"
        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": self._apns_topic,
            "apns-push-type": "mdm",
        }
        try:
            async with httpx.AsyncClient(http2=True, timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(url, headers=headers, json={})
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "apple", f"APNs push timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "apple", f"APNs push unreachable: {exc}"
            ) from exc
        if response.status_code != 200:
            _classify_http_error("APNs push", response)

    async def health_check(self) -> tuple[bool, str | None]:
        """Attempts to mint (or reuse a cached) provider token and confirms
        the signing key file is readable — the cheapest real signal
        without sending an actual push to any device. Never raises."""
        try:
            await self._get_provider_token()
        except (PlatformProviderUnavailableError, PlatformCommandRejectedError) as exc:
            return False, exc.message
        return True, None
