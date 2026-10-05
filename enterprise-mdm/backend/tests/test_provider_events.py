"""Provider-event ingestion + processing (Phase 7 ingestion, Phase 8
processing/resolution). `ProviderEventService.ingest_event` is a callable
service method only; no router exists for ingestion itself (see that
module's docstring), so these tests call it directly, matching the
established pattern for internal capabilities the public API doesn't
expose (see tests/test_command_state_machine.py's identical rationale for
CommandService)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.commands.service import CommandService
from app.devices.enums import DevicePlatform, DeviceType, EnrollmentStatus, OwnershipType
from app.devices.models import Device
from app.jobs.models import JobType
from app.jobs.service import JobService
from app.organizations.models import Organization
from app.platform.registry import PlatformProviderRegistry
from app.provider_events.models import ProviderEventProcessingStatus
from app.provider_events.service import ProviderEventService
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.repositories.provider_event_repository import ProviderEventRepository
from app.users.models import User


def _service(db_session: AsyncSession) -> ProviderEventService:
    return ProviderEventService(
        ProviderEventRepository(db_session),
        DeviceRepository(db_session),
        ProviderDeviceLinkRepository(db_session),
        db_session,
        AuditRepository(db_session),
    )


def _command_service(db_session: AsyncSession) -> CommandService:
    return CommandService(
        DeviceCommandRepository(db_session),
        DeviceRepository(db_session),
        PlatformProviderRegistry(),
        AuditRepository(db_session),
        JobService(BackgroundJobRepository(db_session), AuditRepository(db_session)),
        ProviderDeviceLinkRepository(db_session),
    )


@pytest.mark.asyncio
async def test_ingest_event_creates_a_row_and_ignores_unrecognized_type(
    db_session: AsyncSession,
) -> None:
    event = await _service(db_session).ingest_event(
        provider_name="android",
        provider_event_id="msg-1",
        event_type="STATUS_REPORT",
        payload={"raw": "value"},
    )
    assert event.provider_name == "android"
    assert event.provider_event_id == "msg-1"
    assert event.device_id is None
    assert event.organization_id is None
    # No handler is registered for "STATUS_REPORT" — not an error.
    assert event.processing_status == ProviderEventProcessingStatus.IGNORED
    assert event.processed_at is not None


@pytest.mark.asyncio
async def test_ingest_event_is_idempotent_on_redelivery(db_session: AsyncSession) -> None:
    """The same (provider_name, provider_event_id) pair delivered twice
    (a real webhook's at-least-once redelivery) must return the original
    row, not create a duplicate or attempt to re-resolve/re-process."""
    service = _service(db_session)
    first = await service.ingest_event(
        provider_name="apple",
        provider_event_id="checkin-42",
        event_type="CheckIn",
        payload={"MessageType": "CheckIn"},
    )
    second = await service.ingest_event(
        provider_name="apple",
        provider_event_id="checkin-42",
        event_type="CheckIn",
        payload={"MessageType": "CheckIn", "extra": "field seen on redelivery"},
    )
    assert second.id == first.id
    assert second.payload == first.payload  # unchanged — the original row wins


@pytest.mark.asyncio
async def test_ingest_event_resolves_device_via_provider_device_link(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    device_repo = DeviceRepository(db_session)
    device = await device_repo.create_for_organization(
        organization_a.id,
        display_name="Provider-Resolvable Device",
        platform=DevicePlatform.ANDROID,
        device_type=DeviceType.PHONE,
        ownership_type=OwnershipType.CORPORATE,
    )
    await ProviderDeviceLinkRepository(db_session).create(
        organization_a.id,
        provider_name="android",
        provider_device_id="enterprises/abc/devices/123",
        device_id=device.id,
    )
    await db_session.commit()

    event = await _service(db_session).ingest_event(
        provider_name="android",
        provider_event_id="msg-resolved",
        event_type="STATUS_REPORT",
        payload={},
        organization_id=organization_a.id,
        provider_device_id="enterprises/abc/devices/123",
    )
    assert event.device_id == device.id


@pytest.mark.asyncio
async def test_ingest_event_skips_device_resolution_without_organization_id(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    """A provider_device_id is only unique per organization — resolving
    without an organization context would be a real cross-tenant
    ambiguity, so resolution is skipped entirely rather than guessed at."""
    device_repo = DeviceRepository(db_session)
    device = await device_repo.create_for_organization(
        organization_a.id,
        display_name="Unreachable Without Org Context",
        platform=DevicePlatform.ANDROID,
        device_type=DeviceType.PHONE,
        ownership_type=OwnershipType.CORPORATE,
    )
    await ProviderDeviceLinkRepository(db_session).create(
        organization_a.id,
        provider_name="android",
        provider_device_id="enterprises/abc/devices/456",
        device_id=device.id,
    )
    await db_session.commit()

    event = await _service(db_session).ingest_event(
        provider_name="android",
        provider_event_id="msg-unresolved",
        event_type="STATUS_REPORT",
        payload={},
        provider_device_id="enterprises/abc/devices/456",
    )
    assert event.device_id is None
    assert event.organization_id is None


@pytest.mark.asyncio
async def test_ingest_event_unresolvable_identifier_leaves_device_id_null(
    db_session: AsyncSession, organization_a: Organization
) -> None:
    event = await _service(db_session).ingest_event(
        provider_name="android",
        provider_event_id="msg-no-match",
        event_type="STATUS_REPORT",
        payload={},
        organization_id=organization_a.id,
        provider_device_id="no-such-identifier",
    )
    assert event.device_id is None
    assert event.organization_id == organization_a.id


@pytest.mark.asyncio
async def test_ingest_event_dedupe_key_is_scoped_per_provider(db_session: AsyncSession) -> None:
    """The same provider_event_id from two different providers must not
    collide — the unique constraint (and the lookup) is keyed on the pair."""
    service = _service(db_session)
    android_event = await service.ingest_event(
        provider_name="android",
        provider_event_id=str(uuid.uuid4().hex[:8]),
        event_type="STATUS_REPORT",
        payload={},
    )
    apple_event = await service.ingest_event(
        provider_name="apple",
        provider_event_id=android_event.provider_event_id,
        event_type="CheckIn",
        payload={},
    )
    assert apple_event.id != android_event.id


# ---------------------------------------------------------------------------
# Phase 8: event-type processing / command-completion bridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_check_in_event_schedules_compliance_evaluation(
    db_session: AsyncSession, organization_a: Organization, device_in_org_a: Device
) -> None:
    event = await _service(db_session).ingest_event(
        provider_name="android",
        provider_event_id="checkin-1",
        event_type="DEVICE_CHECK_IN",
        payload={},
        organization_id=organization_a.id,
    )
    # Not resolved to a device via provider_device_id, so the handler must
    # reject it — DEVICE_CHECK_IN requires event.device_id, and nothing
    # populated it here.
    assert event.processing_status == ProviderEventProcessingStatus.FAILED

    await ProviderDeviceLinkRepository(db_session).create(
        organization_a.id,
        provider_name="android",
        provider_device_id="enterprises/abc/devices/checkin",
        device_id=device_in_org_a.id,
    )
    await db_session.commit()

    resolved_event = await _service(db_session).ingest_event(
        provider_name="android",
        provider_event_id="checkin-2",
        event_type="DEVICE_CHECK_IN",
        payload={},
        organization_id=organization_a.id,
        provider_device_id="enterprises/abc/devices/checkin",
    )
    assert resolved_event.device_id == device_in_org_a.id
    assert resolved_event.processing_status == ProviderEventProcessingStatus.PROCESSED

    job_repo = BackgroundJobRepository(db_session)
    active = await job_repo.get_active_by_dedup_key(
        organization_a.id,
        JobType.COMPLIANCE_EVALUATION,
        f"compliance_evaluation:{device_in_org_a.id}",
    )
    assert active is not None


@pytest.mark.asyncio
async def test_command_completed_event_completes_a_sent_command(
    db_session: AsyncSession,
    organization_a: Organization,
    device_in_org_a: Device,
    org_a_admin_user: User,
) -> None:
    device_repo = DeviceRepository(db_session)
    await device_repo.update_for_organization(
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
    assert command.status == DeviceCommandStatus.SENT
    assert command.provider_command_id is not None

    event = await _service(db_session).ingest_event(
        provider_name="mock",
        provider_event_id="completion-1",
        event_type="COMMAND_COMPLETED",
        payload={
            "provider_command_id": command.provider_command_id,
            "result": {"note": "device rebooted"},
        },
        organization_id=organization_a.id,
    )
    assert event.processing_status == ProviderEventProcessingStatus.PROCESSED

    final = await DeviceCommandRepository(db_session).get_for_organization(
        organization_a.id, command.id
    )
    assert final is not None
    assert final.status == DeviceCommandStatus.COMPLETED
    assert final.result == {"note": "device rebooted"}


@pytest.mark.asyncio
async def test_command_completed_event_redelivery_does_not_double_complete(
    db_session: AsyncSession,
    organization_a: Organization,
    device_in_org_a: Device,
    org_a_admin_user: User,
) -> None:
    """Event #123 processed once must never be re-applied on redelivery —
    no duplicate completion, no duplicate audit event (Phase 8 §10)."""
    device_repo = DeviceRepository(db_session)
    await device_repo.update_for_organization(
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

    service = _service(db_session)
    payload = {"provider_command_id": command.provider_command_id}
    first = await service.ingest_event(
        provider_name="mock",
        provider_event_id="completion-redeliver",
        event_type="COMMAND_COMPLETED",
        payload=payload,
        organization_id=organization_a.id,
    )
    assert first.processing_status == ProviderEventProcessingStatus.PROCESSED

    # Redelivered: same provider_event_id. apply_provider_event would
    # reject a second SENT->COMPLETED transition on an already-COMPLETED
    # command, but ingest_event must not even attempt it — the idempotency
    # check short-circuits before the handler ever runs again.
    second = await service.ingest_event(
        provider_name="mock",
        provider_event_id="completion-redeliver",
        event_type="COMMAND_COMPLETED",
        payload=payload,
        organization_id=organization_a.id,
    )
    assert second.id == first.id
    assert second.processed_at == first.processed_at


@pytest.mark.asyncio
async def test_command_completed_event_without_organization_fails_safely(
    db_session: AsyncSession,
) -> None:
    """No organization_id means no safe way to scope the
    provider_command_id lookup — must fail, never guess."""
    event = await _service(db_session).ingest_event(
        provider_name="mock",
        provider_event_id="orphan-completion",
        event_type="COMMAND_COMPLETED",
        payload={"provider_command_id": "mock-doesnt-matter"},
    )
    assert event.processing_status == ProviderEventProcessingStatus.FAILED
    assert event.processing_error is not None
    assert "organization" in event.processing_error.lower()


@pytest.mark.asyncio
async def test_command_failed_event_requires_command_still_sent(
    db_session: AsyncSession,
    organization_a: Organization,
    device_in_org_a: Device,
    org_a_admin_user: User,
) -> None:
    """A command already COMPLETED can never be pushed to FAILED by a
    late/out-of-order event — apply_provider_event only accepts SENT."""
    device_repo = DeviceRepository(db_session)
    await device_repo.update_for_organization(
        device_in_org_a, enrollment_status=EnrollmentStatus.ENROLLED
    )
    await db_session.commit()

    command_service = _command_service(db_session)
    command = await command_service.create_command(
        organization_a.id,
        device_in_org_a.id,
        command_type=DeviceCommandType.LOCK,
        payload=None,
        idempotency_key=None,
        actor_user_id=org_a_admin_user.id,
    )
    await db_session.commit()
    # No simulate_sent -> Mock resolves straight to COMPLETED.
    command = await command_service.process_dispatch_job(organization_a.id, command.id)
    assert command.status == DeviceCommandStatus.COMPLETED

    event = await _service(db_session).ingest_event(
        provider_name="mock",
        provider_event_id="late-failure",
        event_type="COMMAND_FAILED",
        payload={"provider_command_id": command.provider_command_id},
        organization_id=organization_a.id,
    )
    assert event.processing_status == ProviderEventProcessingStatus.FAILED

    unchanged = await DeviceCommandRepository(db_session).get_for_organization(
        organization_a.id, command.id
    )
    assert unchanged is not None
    assert unchanged.status == DeviceCommandStatus.COMPLETED
