"""Phase 15 — baseline security response headers
(app/core/security_headers.py::SecurityHeadersMiddleware).

Does not re-test CSRF/CORS/request-ID behavior — those are Phase 1-5's
own, unchanged, already-covered elsewhere (tests/test_security.py).
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_security_headers_present_on_every_response(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["X-Permitted-Cross-Domain-Policies"] == "none"


async def test_hsts_absent_without_cookie_secure(client: AsyncClient) -> None:
    """The test harness runs with the default `cookie_secure=False` (local
    dev over HTTP) — HSTS must not be sent in that configuration, since it
    would wrongly instruct a browser to require HTTPS for a host that
    isn't serving it."""
    response = await client.get("/health")
    assert "Strict-Transport-Security" not in response.headers


async def test_security_headers_present_on_error_response(client: AsyncClient) -> None:
    """Headers apply regardless of status code — a 404 must be exactly as
    protected as a 200."""
    response = await client.get("/api/v1/this-route-does-not-exist")
    assert response.status_code == 404
    assert response.headers["X-Frame-Options"] == "DENY"
