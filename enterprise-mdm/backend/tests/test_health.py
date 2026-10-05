from __future__ import annotations

from httpx import AsyncClient


async def test_health_endpoint_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "EnterpriseMDM"


async def test_health_endpoint_available_under_api_v1(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200


async def test_ready_endpoint_reports_dependency_status(client: AsyncClient) -> None:
    response = await client.get("/ready")
    # Dependencies may be unavailable in a unit-test environment without
    # Postgres/Redis running; assert on shape and status-code consistency,
    # not a hard 200, so this test is meaningful in CI without live infra.
    assert response.status_code in (200, 503)
    body = response.json()
    assert "dependencies" in body
    assert set(body["dependencies"].keys()) == {"database", "redis"}


async def test_error_envelope_shape_on_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/this-route-does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert set(body["error"].keys()) == {"code", "message", "request_id"}


async def test_request_id_header_present(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert "x-request-id" in {k.lower() for k in response.headers.keys()}
