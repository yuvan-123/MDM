"""Android Management API transport client (Phase 9).

Separates HTTP/auth transport from app/platform/android/provider.py's
domain-verb mapping (LOCK/REBOOT/WIPE/... -> issueCommand), per this
phase's "keep API transport separate from MDM domain logic" instruction.

## Token caching
`PlatformProviderRegistry.get_provider` (app/platform/registry.py)
constructs a brand-new `AndroidManagementProvider` on every call — a
deliberate, already-audited design (so a registry built from an explicit
Settings override, as every test does, is always the source of truth) that
this phase does not change. An instance-level token cache would therefore
never actually persist across calls: every dispatch would still mint a
fresh token. So the cache here is process-wide module state instead,
mirroring the existing singleton pattern in `app/core/redis.py::get_redis_client`
(`global _redis_client`) — the only established precedent for this shape
in the codebase. Keyed by the service account's own `client_email` so a
config change (different credentials, e.g. between test cases using
different `Settings` overrides) can never serve a stale token minted for a
different account.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import jwt

from app.platform.base.errors import PlatformCommandRejectedError, PlatformProviderUnavailableError

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_API_BASE = "https://androidmanagement.googleapis.com/v1"
_SCOPE = "https://www.googleapis.com/auth/androidmanagement"
_REQUEST_TIMEOUT_SECONDS = 10.0
# Refresh this long before the token's actual expiry, so a request that
# starts just before expiry never races a mid-flight 401 from Google.
_TOKEN_REFRESH_MARGIN_SECONDS = 300.0

# Process-wide cache: {client_email: (access_token, expires_at_epoch_seconds)}.
_token_cache: dict[str, tuple[str, float]] = {}
_token_cache_lock = asyncio.Lock()


def _classify_http_error(provider_name: str, operation: str, response: httpx.Response) -> None:
    """5xx and 429 are the provider's own signal that this specific attempt
    failed for a reason retrying might fix — mapped to
    PlatformProviderUnavailableError, which app/jobs/retry.py::is_transient
    already treats as retryable. Every other 4xx (bad request, invalid
    device, revoked auth, ...) is permanent — PlatformCommandRejectedError,
    not retried."""
    if response.status_code == 429 or response.status_code >= 500:
        raise PlatformProviderUnavailableError(
            provider_name, f"{operation} transiently failed: HTTP {response.status_code}"
        )
    raise PlatformCommandRejectedError(
        provider_name, f"{operation} failed: HTTP {response.status_code} {response.text[:200]}"
    )


class AndroidApiClient:
    def __init__(self, service_account_json: str, enterprise_id: str) -> None:
        self._service_account_json = service_account_json
        self._enterprise_id = enterprise_id

    @property
    def enterprise_id(self) -> str:
        return self._enterprise_id

    def _load_account(self) -> dict[str, Any]:
        try:
            account: dict[str, Any] = json.loads(self._service_account_json)
            _ = account["client_email"], account["private_key"]  # required keys, fail fast
        except (ValueError, KeyError, TypeError) as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Malformed service account credentials: {exc}"
            ) from exc
        return account

    async def _mint_token(self, account: dict[str, Any]) -> tuple[str, float]:
        now = int(time.time())
        assertion = jwt.encode(
            {
                "iss": account["client_email"],
                "scope": _SCOPE,
                "aud": _TOKEN_URL,
                "iat": now,
                "exp": now + 3600,
            },
            account["private_key"],
            algorithm="RS256",
        )
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"OAuth token endpoint timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"OAuth token endpoint unreachable: {exc}"
            ) from exc

        if response.status_code != 200:
            _classify_http_error("android", "OAuth token exchange", response)
        body = response.json()
        access_token: str = body["access_token"]
        expires_in = float(body.get("expires_in", 3600))
        return access_token, time.time() + expires_in

    async def _get_access_token(self) -> str:
        account = self._load_account()
        client_email: str = account["client_email"]

        async with _token_cache_lock:
            cached = _token_cache.get(client_email)
            if cached is not None:
                token, expires_at = cached
                if time.time() < expires_at - _TOKEN_REFRESH_MARGIN_SECONDS:
                    return token
            token, expires_at = await self._mint_token(account)
            _token_cache[client_email] = (token, expires_at)
            return token

    async def issue_command(
        self, provider_device_id: str, command_type: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        access_token = await self._get_access_token()
        url = (
            f"{_API_BASE}/enterprises/{self._enterprise_id}/devices/"
            f"{provider_device_id}:issueCommand"
        )
        body = {"type": command_type, **params}
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url, json=body, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"issueCommand({command_type}) timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"issueCommand({command_type}) unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            _classify_http_error("android", f"issueCommand({command_type})", response)
        result: dict[str, Any] = response.json()
        return result

    async def patch_device(
        self, provider_device_id: str, update_mask: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        access_token = await self._get_access_token()
        url = f"{_API_BASE}/enterprises/{self._enterprise_id}/devices/{provider_device_id}"
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.patch(
                    url,
                    params={"updateMask": update_mask},
                    json=body,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Device patch timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Device patch unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            _classify_http_error("android", "Device patch", response)
        result: dict[str, Any] = response.json()
        return result

    async def get_policy(self, policy_name: str) -> dict[str, Any]:
        """`enterprises.policies.get` — Phase 14: reads the full Policy
        resource (including its `applications` array) before patching it in
        `patch_policy` below. Android Management API's PATCH replaces the
        entire field named in `updateMask`, not a partial per-element
        merge, so a caller must read-modify-write the whole array — the
        same pattern Google's own documented policy-update examples use."""
        access_token = await self._get_access_token()
        url = f"{_API_BASE}/{policy_name}"
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Policy get timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Policy get unreachable: {exc}"
            ) from exc
        if response.status_code == 404:
            # A policy that has never been created (e.g. no device has ever
            # applied POLICY_SYNC yet) returns 404 — treated as "an empty
            # policy" by the caller, not an error, since app deployment must
            # work even for the very first app assigned in this deployment.
            return {}
        if response.status_code >= 400:
            _classify_http_error("android", "Policy get", response)
        result: dict[str, Any] = response.json()
        return result

    async def patch_policy(
        self, policy_name: str, update_mask: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """`enterprises.policies.patch` — Phase 14: same
        create-if-absent-on-first-write semantics as Google's documented
        policy PATCH (a policy resource name that doesn't yet exist is
        created by the first PATCH to it)."""
        access_token = await self._get_access_token()
        url = f"{_API_BASE}/{policy_name}"
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.patch(
                    url,
                    params={"updateMask": update_mask},
                    json=body,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Policy patch timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Policy patch unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            _classify_http_error("android", "Policy patch", response)
        result: dict[str, Any] = response.json()
        return result

    async def create_enrollment_token(
        self, *, policy_name: str, duration_seconds: int
    ) -> dict[str, Any]:
        """`enterprises.enrollmentTokens.create` — the real Android
        Management API mechanism a device provisions itself against
        directly (QR code / NFC / zero-touch), entirely bypassing this
        backend until it later checks in under `policy_name`. Response
        includes `value` (the secret a device uses — treat exactly like
        this codebase's own raw enrollment token: return once, never log),
        `name` (a non-secret resource name, safe to persist), `qrCode`, and
        `expirationTimestamp`.

        `allowPersonalUsage=PERSONAL_USAGE_ALLOWED` — without it, Google's
        own default is `PERSONAL_USAGE_DISALLOWED`, which silently rejects
        provisioning on any personally-owned device that would enroll as a
        work profile (Google's documented requirement for that flow) — a
        device-owner/fully-managed enrollment via the same token is
        unaffected either way. This codebase's enrollment-token creation
        (`app/enrollment/service.py`) doesn't yet thread a per-token
        ownership-type choice through to here, so this defaults every
        token to the more permissive setting rather than silently blocking
        the common BYOD/work-profile case — discovered only by an actual
        personal device hitting exactly this rejection, since no earlier
        phase's testing ever used a real device."""
        access_token = await self._get_access_token()
        url = f"{_API_BASE}/enterprises/{self._enterprise_id}/enrollmentTokens"
        body = {
            "policyName": policy_name,
            "duration": f"{duration_seconds}s",
            "allowPersonalUsage": "PERSONAL_USAGE_ALLOWED",
        }
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    url, json=body, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"createEnrollmentToken timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"createEnrollmentToken unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            _classify_http_error("android", "createEnrollmentToken", response)
        result: dict[str, Any] = response.json()
        return result

    async def get_device_resource(self, provider_device_id: str) -> dict[str, Any]:
        """`enterprises.devices.get` — the full Device resource, including
        `policyName` and `enrollmentTokenName` (the exact `reference`
        value `create_enrollment_token` returned when this device was
        enrolled). Deliberately separate from
        `AndroidManagementProvider.get_device` (the REFRESH_INFO capability,
        which *asks the device* for fresh info via issueCommand) — this is
        a synchronous read of Google's own already-cached resource, used
        only to resolve a first-time webhook check-in's organization (see
        app/platform/android/router.py), never to answer a REFRESH_INFO
        command."""
        access_token = await self._get_access_token()
        url = f"{_API_BASE}/enterprises/{self._enterprise_id}/devices/{provider_device_id}"
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    url, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Device get timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Device get unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            _classify_http_error("android", "Device get", response)
        result: dict[str, Any] = response.json()
        return result

    async def delete_device(self, provider_device_id: str) -> None:
        """`enterprises.devices.delete` — removes the device from real
        Android management entirely. Documented in
        app/platform/android/provider.py's own module docstring since
        Phase 6 as deliberately NOT used for WIPE (it's a "blunter" action
        than issueCommand(WIPE): it attempts a best-effort wipe AND removes
        the device resource from the enterprise) — Phase 12 is the first
        phase with a genuine use for it: real UNENROLL. Unlike
        `issue_command`, this is a direct synchronous REST delete, not a
        long-running Operation — Google returns an empty body on success,
        so there is nothing to return here beyond confirming it did not
        error."""
        access_token = await self._get_access_token()
        url = f"{_API_BASE}/enterprises/{self._enterprise_id}/devices/{provider_device_id}"
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.delete(
                    url, headers={"Authorization": f"Bearer {access_token}"}
                )
        except httpx.TimeoutException as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Device delete timed out: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise PlatformProviderUnavailableError(
                "android", f"Device delete unreachable: {exc}"
            ) from exc
        if response.status_code >= 400:
            _classify_http_error("android", "Device delete", response)

    async def health_check(self) -> tuple[bool, str | None]:
        """Attempts to mint (or reuse a cached) access token — the cheapest
        real signal that credentials are valid and Google's token endpoint
        is reachable, without touching any actual device. Never raises;
        used by the Phase 9 provider-health admin endpoint."""
        try:
            await self._get_access_token()
        except (PlatformProviderUnavailableError, PlatformCommandRejectedError) as exc:
            return False, exc.message
        return True, None
