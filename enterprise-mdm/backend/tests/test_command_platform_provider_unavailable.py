"""The "honesty" test (Phase 6): when a real provider is selected but has
no credentials configured — which is always true in this development
environment — a command must fail loudly (FAILED, with a clear error code)
rather than silently succeed or silently fall back to Mock. See
docs/architecture/device-commands.md and app/platform/registry.py.

Constructs CommandService directly with a PlatformProviderRegistry built
from a Settings override, rather than going through the HTTP API — there is
no public way (and there should never be one) for an API caller to flip
platform_provider_mode per-request; it is a deployment-level setting.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.commands.service import CommandService
from app.core.config import Settings
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User
from tests.conftest import _login
from tests.test_commands import _enroll_device


@pytest.mark.asyncio
async def test_real_provider_mode_without_credentials_fails_loudly(
    client: AsyncClient,
    org_a_admin_user: User,
    organization_a: Organization,
    db_session: AsyncSession,
) -> None:
    csrf = await _login(client, org_a_admin_user.email, "OrgAAdminPass123!")
    device_id, _ = await _enroll_device(client, csrf)

    real_mode_settings = Settings(platform_provider_mode="real")
    service = CommandService(
        command_repo=DeviceCommandRepository(db_session),
        device_repo=DeviceRepository(db_session),
        provider_registry=PlatformProviderRegistry(settings=real_mode_settings),
        audit_repo=AuditRepository(db_session),
        job_service=JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db_session),
    )

    command = await service.create_command(
        organization_a.id,
        uuid.UUID(device_id),
        command_type=DeviceCommandType.LOCK,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    assert command.status == DeviceCommandStatus.PENDING
    await db_session.commit()

    command = await service.process_dispatch_job(organization_a.id, command.id)
    assert command.status == DeviceCommandStatus.FAILED
    assert command.error_code == "PLATFORM_PROVIDER_UNAVAILABLE"
    assert command.error_message is not None
    assert "not available" in command.error_message.lower()
    # Never a silent success, and never silently substituted with Mock.
    assert command.provider_name != "mock"
    assert command.result is None
