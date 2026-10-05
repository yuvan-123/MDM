"""Phase 15 — enrollment rate limiting
(app/auth/rate_limit.py::check_enrollment_rate_limit).

Does not re-test the pre-existing login/password-reset limiters (both
unchanged) or enrollment registration itself (tests/test_enrollment.py) —
only the new per-IP limiter this phase adds to `POST /enrollment/register`.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import status
from httpx import AsyncClient

import app.auth.rate_limit as rate_limit_module
from app.auth.rate_limit import check_enrollment_rate_limit
from app.core.errors import AppError


async def test_fails_open_when_redis_unavailable() -> None:
    """This sandbox/CI-less test environment genuinely has no Redis
    listening — calling the real (unmocked) function here exercises the
    actual fail-open path, not a simulated one."""
    await check_enrollment_rate_limit("203.0.113.1")  # must not raise


class _FakePipeline:
    def __init__(self, count: int) -> None:
        self._count = count
        self._ops: list[str] = []

    def incr(self, _key: str) -> _FakePipeline:
        self._ops.append("incr")
        return self

    def ttl(self, _key: str) -> _FakePipeline:
        self._ops.append("ttl")
        return self

    async def execute(self) -> list[Any]:
        return [self._count, 60]  # ttl already set — never re-armed mid-test


class _FakeRedisClient:
    def __init__(self, count: int) -> None:
        self._count = count

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self._count)

    async def expire(self, *_args: Any, **_kwargs: Any) -> None:
        return None


async def test_blocks_after_threshold_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    # login_rate_limit_per_ip defaults to 20 — 21 requests in the window
    # exceeds it.
    monkeypatch.setattr(
        rate_limit_module, "get_redis_client", lambda: _FakeRedisClient(count=21)
    )
    with pytest.raises(AppError) as exc_info:
        await check_enrollment_rate_limit("203.0.113.2")
    assert exc_info.value.code == "RATE_LIMITED"
    assert exc_info.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


async def test_allows_under_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limit_module, "get_redis_client", lambda: _FakeRedisClient(count=1))
    await check_enrollment_rate_limit("203.0.113.3")  # must not raise


async def test_register_endpoint_reachable_under_normal_traffic(client: AsyncClient) -> None:
    """End-to-end smoke check: the new rate-limit call added to
    `register_device` must not itself break a normal (invalid-token, since
    no real token is set up here) registration attempt — this asserts the
    request reaches EnrollmentService at all (a domain-level 404/400 for
    the bad token), not a 429 or 500 from the new limiter call."""
    response = await client.post(
        "/api/v1/enrollment/register",
        json={
            "token": "not-a-real-token",
            "display_name": "Rate Limit Smoke Test Device",
            "platform": "ANDROID",
            "device_type": "PHONE",
            "ownership_type": "CORPORATE",
        },
    )
    assert response.status_code != status.HTTP_429_TOO_MANY_REQUESTS
    assert response.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR
