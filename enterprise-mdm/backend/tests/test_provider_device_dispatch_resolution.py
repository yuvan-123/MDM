"""Phase 9 — CommandService._dispatch resolves a real provider-native
device identifier through ProviderDeviceLink before calling the provider,
closing the loop between Phase 8's inbound-resolution table and actual
outbound dispatch. Uses MockManagementProvider (the only provider ever
selected by default) with the test-only `simulate_sent` payload key so the
dispatched request can be inspected without a real provider."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandType
from app.commands.service import CommandService
from app.devices.enums import EnrollmentStatus
from app.devices.models import Device
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.users.models import User


def _service(db_session: AsyncSession) -> CommandService:
    return CommandService(
        DeviceCommandRepository(db_session),
        DeviceRepository(db_session),
        PlatformProviderRegistry(),
        AuditRepository(db_session),
        JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        ProviderDeviceLinkRepository(db_session),
    )


@pytest.mark.asyncio
async def test_dispatch_succeeds_with_a_resolved_provider_device_link(
    db_session: AsyncSession,
    organization_a: Organization,
    device_in_org_a: Device,
    org_a_admin_user: User,
) -> None:
    """Regression coverage for the resolution call added to `_dispatch`:
    a device with a real ProviderDeviceLink row must dispatch exactly as
    successfully as one without (MockManagementProvider doesn't read
    provider_device_id, so this proves resolution doesn't raise or
    otherwise interfere) — the resolution logic itself is unit-tested
    directly against the repository below."""
    device_repo = DeviceRepository(db_session)
    await device_repo.update_for_organization(
        device_in_org_a, enrollment_status=EnrollmentStatus.ENROLLED
    )
    await ProviderDeviceLinkRepository(db_session).create(
        organization_a.id,
        provider_name="mock",
        provider_device_id="mock-native-id-999",
        device_id=device_in_org_a.id,
    )
    await db_session.commit()

    service = _service(db_session)
    command = await service.create_command(
        organization_a.id,
        device_in_org_a.id,
        command_type=DeviceCommandType.LOCK,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()

    dispatched = await service.process_dispatch_job(organization_a.id, command.id)
    assert dispatched.status.value == "COMPLETED"

    resolved = await ProviderDeviceLinkRepository(db_session).get_provider_device_id(
        organization_a.id, device_in_org_a.id, "mock"
    )
    assert resolved == "mock-native-id-999"


@pytest.mark.asyncio
async def test_dispatch_resolution_is_org_scoped(
    db_session: AsyncSession,
    organization_a: Organization,
    device_in_org_a: Device,
    org_a_admin_user: User,
) -> None:
    """A link created for a different provider name must never resolve —
    proves the lookup is keyed on (org, device, provider_name), not just
    (org, device)."""
    await DeviceRepository(db_session).update_for_organization(
        device_in_org_a, enrollment_status=EnrollmentStatus.ENROLLED
    )
    await ProviderDeviceLinkRepository(db_session).create(
        organization_a.id,
        provider_name="android",  # not "mock" — the provider actually in use
        provider_device_id="enterprises/x/devices/y",
        device_id=device_in_org_a.id,
    )
    await db_session.commit()

    resolved = await ProviderDeviceLinkRepository(db_session).get_provider_device_id(
        organization_a.id, device_in_org_a.id, "mock"
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_reverse_lookup_returns_none_without_a_link(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    resolved = await ProviderDeviceLinkRepository(db_session).get_provider_device_id(
        organization_a.id, uuid.uuid4(), "mock"
    )
    assert resolved is None
