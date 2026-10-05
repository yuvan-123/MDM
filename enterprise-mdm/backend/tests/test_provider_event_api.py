"""Provider-event admin API (Phase 8) — RBAC gating and tenant isolation.
See app/provider_events/router.py and docs/architecture/provider-events.md."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.devices.enums import EnrollmentStatus
from app.devices.models import Device
from app.organizations.models import Organization
from app.provider_events.service import ProviderEventService
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.repositories.provider_event_repository import ProviderEventRepository
from app.users.models import User
from tests.conftest import _login
from tests.test_provider_events import _command_service


def _service(db_session: AsyncSession) -> ProviderEventService:
    return ProviderEventService(
        ProviderEventRepository(db_session),
        DeviceRepository(db_session),
        ProviderDeviceLinkRepository(db_session),
        db_session,
        AuditRepository(db_session),
    )


async def _seed_event(
    db_session: AsyncSession, organization: Organization, *, event_id: str | None = None
) -> str:
    service = _service(db_session)
    event = await service.ingest_event(
        provider_name="android",
        provider_event_id=event_id or str(uuid.uuid4()),
        event_type="STATUS_REPORT",
        payload={"note": "test event"},
        organization_id=organization.id,
    )
    await db_session.commit()
    return str(event.id)


@pytest.mark.asyncio
async def test_organization_admin_can_list_and_get_provider_events(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    event_id = await _seed_event(db_session, organization_a)
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    list_res = await client.get("/api/v1/provider-events", headers={"X-CSRF-Token": csrf})
    assert list_res.status_code == 200, list_res.text
    assert any(item["id"] == event_id for item in list_res.json()["items"])

    get_res = await client.get(
        f"/api/v1/provider-events/{event_id}", headers={"X-CSRF-Token": csrf}
    )
    assert get_res.status_code == 200
    assert get_res.json()["processing_status"] == "IGNORED"  # no handler for STATUS_REPORT


@pytest.mark.asyncio
async def test_provider_event_list_filters_by_provider_and_status(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    await _seed_event(db_session, organization_a)
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    matching = await client.get(
        "/api/v1/provider-events?provider_name=android&status=IGNORED",
        headers={"X-CSRF-Token": csrf},
    )
    assert matching.status_code == 200
    assert matching.json()["total"] >= 1

    empty = await client.get(
        "/api/v1/provider-events?provider_name=apple", headers={"X-CSRF-Token": csrf}
    )
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


@pytest.mark.asyncio
async def test_org_a_cannot_see_org_b_provider_events(
    client: AsyncClient,
    org_a_admin_user: User,
    org_b_admin_user: User,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    event_id = await _seed_event(db_session, organization_b)

    a_csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    list_res = await client.get("/api/v1/provider-events", headers={"X-CSRF-Token": a_csrf})
    assert list_res.status_code == 200
    assert all(item["id"] != event_id for item in list_res.json()["items"])

    get_res = await client.get(
        f"/api/v1/provider-events/{event_id}", headers={"X-CSRF-Token": a_csrf}
    )
    assert get_res.status_code == 404
    assert get_res.json()["error"]["code"] == "PROVIDER_EVENT_NOT_FOUND"

    b_csrf = await _login(client, org_b_admin_user.email, "OrgBAdminPass123!")
    own_get = await client.get(
        f"/api/v1/provider-events/{event_id}", headers={"X-CSRF-Token": b_csrf}
    )
    assert own_get.status_code == 200


@pytest.mark.asyncio
async def test_unresolved_organization_event_is_invisible_to_every_org(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    """An event ingested without an organization_id (e.g. the caller had no
    tenant context to supply) has no admin-API home — this is the accepted
    Phase 8 boundary documented in docs/architecture/provider-events.md §5."""
    service = _service(db_session)
    orphan = await service.ingest_event(
        provider_name="android",
        provider_event_id=str(uuid.uuid4()),
        event_type="STATUS_REPORT",
        payload={},
    )
    await db_session.commit()
    assert orphan.organization_id is None

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.get(
        f"/api/v1/provider-events/{orphan.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_help_desk_cannot_read_provider_events(
    client: AsyncClient, org_a_member_user: User, organization_a: Organization
) -> None:
    csrf = await _login(client, org_a_member_user.email, "OrgAMemberPass123!")
    res = await client.get("/api/v1/provider-events", headers={"X-CSRF-Token": csrf})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Phase 10: manual retry
# ---------------------------------------------------------------------------


async def _seed_failed_event(
    db_session: AsyncSession, organization: Organization, *, provider_command_id: str
) -> str:
    """A COMMAND_COMPLETED event referencing a provider_command_id that
    matches no existing command fails processing (apply_provider_event
    raises COMMAND_NOT_FOUND) while still keeping organization_id set —
    i.e. FAILED but visible through the org-scoped admin API, unlike an
    unresolved-organization event."""
    service = _service(db_session)
    event = await service.ingest_event(
        provider_name="mock",
        provider_event_id=str(uuid.uuid4()),
        event_type="COMMAND_COMPLETED",
        payload={"provider_command_id": provider_command_id},
        organization_id=organization.id,
    )
    await db_session.commit()
    assert event.processing_status.value == "FAILED"
    return str(event.id)


@pytest.mark.asyncio
async def test_organization_admin_can_retry_a_failed_event(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    event_id = await _seed_failed_event(
        db_session, organization_a, provider_command_id="still-missing"
    )
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    res = await client.post(
        f"/api/v1/provider-events/{event_id}/retry", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200, res.text
    # Nothing changed about the underlying condition (still no matching
    # command), so retrying must fail again identically — proves retry
    # actually re-runs processing rather than just flipping a status flag.
    assert res.json()["processing_status"] == "FAILED"


@pytest.mark.asyncio
async def test_retry_can_succeed_once_the_underlying_condition_is_fixed(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    device_in_org_a: Device,
    db_session: AsyncSession,
) -> None:
    """The realistic operator workflow: an event failed because the
    command it referenced didn't exist yet (e.g. arrived out of order);
    once the command exists, retrying the same event now succeeds and
    completes it — proving retry is a genuine re-attempt, not a no-op."""
    event_id = await _seed_failed_event(
        db_session, organization_a, provider_command_id="will-exist-soon"
    )

    await DeviceRepository(db_session).update_for_organization(
        device_in_org_a, enrollment_status=EnrollmentStatus.ENROLLED
    )
    await db_session.commit()
    command_service = _command_service(db_session)
    command = await command_service.create_command(
        organization_a.id,
        device_in_org_a.id,
        command_type=DeviceCommandType.REBOOT,
        payload={"simulate_sent": True},
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()
    command = await command_service.process_dispatch_job(organization_a.id, command.id)
    await DeviceCommandRepository(db_session).update_for_organization(
        command, status=DeviceCommandStatus.SENT, provider_command_id="will-exist-soon"
    )
    await db_session.commit()

    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    res = await client.post(
        f"/api/v1/provider-events/{event_id}/retry", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200, res.text
    assert res.json()["processing_status"] == "PROCESSED"

    final = await DeviceCommandRepository(db_session).get_for_organization(
        organization_a.id, command.id
    )
    assert final is not None
    assert final.status == DeviceCommandStatus.COMPLETED


@pytest.mark.asyncio
async def test_cannot_retry_a_processed_event(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    event_id = await _seed_event(db_session, organization_a)  # IGNORED, not FAILED
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    res = await client.post(
        f"/api/v1/provider-events/{event_id}/retry", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "PROVIDER_EVENT_NOT_RETRYABLE"


@pytest.mark.asyncio
async def test_org_a_cannot_retry_org_b_event(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_b: Organization,
    db_session: AsyncSession,
) -> None:
    event_id = await _seed_failed_event(db_session, organization_b, provider_command_id="x")
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")

    res = await client.post(
        f"/api/v1/provider-events/{event_id}/retry", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_auditor_cannot_retry_provider_events(
    client: AsyncClient,
    org_a_auditor_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    event_id = await _seed_failed_event(db_session, organization_a, provider_command_id="x")
    csrf = await _login(client, org_a_auditor_user.email, "OrgAAuditorPass123!")

    res = await client.post(
        f"/api/v1/provider-events/{event_id}/retry", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 403
