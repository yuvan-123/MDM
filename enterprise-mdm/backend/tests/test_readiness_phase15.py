"""Phase 15 — `/ready` no longer gates on Redis (app/api/v1/health.py).

Redis in this codebase is exclusively a fail-open rate-limit cache
(app/auth/rate_limit.py) — the database remains the sole hard gate for
overall readiness. Monkeypatches the two dependency-check functions
directly (no live Postgres/Redis needed) to exercise all four
availability combinations; tests/test_health.py's own pre-existing test
(shape/status-code-in-(200,503) against whatever real infra happens to be
reachable) is untouched.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

import app.api.v1.health as health_module


def _patch(monkeypatch: pytest.MonkeyPatch, *, db_ok: bool, redis_ok: bool) -> None:
    async def _db() -> bool:
        return db_ok

    async def _redis() -> bool:
        return redis_ok

    monkeypatch.setattr(health_module, "check_database_connection", _db)
    monkeypatch.setattr(health_module, "check_redis_connection", _redis)


async def test_ready_when_database_and_redis_ok(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, db_ok=True, redis_ok=True)
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_ready_when_database_ok_but_redis_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Phase 15 behavior change: a Redis outage alone must not take
    this instance out of service — it only degrades rate-limiting, which
    already fails open."""
    _patch(monkeypatch, db_ok=True, redis_ok=False)
    response = await client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["redis"] == "unavailable"
    assert body["dependencies"]["database"] == "ok"


async def test_not_ready_when_database_down_even_if_redis_ok(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Database remains a hard gate — unchanged from before this phase."""
    _patch(monkeypatch, db_ok=False, redis_ok=True)
    response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


async def test_not_ready_when_both_down(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch(monkeypatch, db_ok=False, redis_ok=False)
    response = await client.get("/ready")
    assert response.status_code == 503
