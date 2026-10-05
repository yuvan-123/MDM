"""Device command state machine (Phase 6, dispatch made async in Phase 8) —
legal/illegal transitions and lazy expiration. See
docs/architecture/device-commands.md and docs/architecture/background-jobs.md §7.

Reaching SENT (rather than Mock's default COMPLETED) requires the
`simulate_sent` payload key documented on MockManagementProvider — that key
is intentionally NOT exposed through any public request schema (an admin
must never be able to tell a real provider to "simulate" anything), so
these tests call CommandService directly against the same db_session the
HTTP `client` fixture shares, rather than only through the router. This is
the correct layer for testing internal state-machine behavior that the
public API deliberately does not expose a hook for.

Command creation only enqueues dispatch as of Phase 8 — every test here
calls `service.process_dispatch_job` (or `tests.conftest.dispatch_pending_command`
for the HTTP-created case) immediately after `create_command`/the POST to
drive dispatch synchronously, exactly what app/workers/loop.py's worker
would do for one claimed COMMAND_DISPATCH job.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandType
from app.commands.service import CommandService
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from tests.conftest import _login, dispatch_pending_command
from tests.test_commands import _enroll_device


def _make_service(db_session: AsyncSession) -> CommandService:
    return CommandService(
        command_repo=DeviceCommandRepository(db_session),
        device_repo=DeviceRepository(db_session),
        provider_registry=PlatformProviderRegistry(),
        audit_repo=AuditRepository(db_session),
        job_service=JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db_session),
    )


@pytest.mark.asyncio
async def test_cannot_cancel_a_completed_command(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    created = await client.post(
        f"/api/v1/devices/{device_id}/commands/lock", headers={"X-CSRF-Token": csrf}, json={}
    )
    assert created.json()["status"] == "PENDING"
    command_id = created.json()["id"]

    dispatched = await dispatch_pending_command(db_session, organization_a.id, command_id)
    assert dispatched.status.value == "COMPLETED"

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/{command_id}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "INVALID_COMMAND_TRANSITION"


@pytest.mark.asyncio
async def test_cancel_from_sent_succeeds(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    service = _make_service(db_session)
    command = await service.create_command(
        organization_a.id,
        uuid.UUID(device_id),
        command_type=DeviceCommandType.REBOOT,
        payload={"simulate_sent": True},
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    assert command.status.value == "PENDING"
    await db_session.commit()

    command = await service.process_dispatch_job(organization_a.id, command.id)
    assert command.status.value == "SENT"

    res = await client.post(
        f"/api/v1/devices/{device_id}/commands/{command.id}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "CANCELLED"

    second = await client.post(
        f"/api/v1/devices/{device_id}/commands/{command.id}/cancel",
        headers={"X-CSRF-Token": csrf},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_sent_command_expires_lazily_on_read(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    service = _make_service(db_session)
    command = await service.create_command(
        organization_a.id,
        uuid.UUID(device_id),
        command_type=DeviceCommandType.REBOOT,
        payload={"simulate_sent": True},
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()
    command = await service.process_dispatch_job(organization_a.id, command.id)
    assert command.status.value == "SENT"

    # Backdate expiry directly via the repository, mirroring how
    # test_expired_token_rejected backdates an enrollment token's
    # expires_at rather than waiting on real wall-clock time.
    repo = DeviceCommandRepository(db_session)
    await repo.update_for_organization(
        command, expires_at=datetime.now(tz=UTC) - timedelta(hours=1)
    )

    res = await client.get(
        f"/api/v1/devices/{device_id}/commands/{command.id}", headers={"X-CSRF-Token": csrf}
    )
    assert res.status_code == 200
    assert res.json()["status"] == "EXPIRED"
