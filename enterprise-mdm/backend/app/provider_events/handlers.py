"""Provider-event processing (Phase 8).

Normalize -> identify event type -> handler -> domain service -> state
change -> audit -> completion. A registry (`_HANDLERS`), not an if/elif
chain — adding a new event type means writing a function and registering
it here, mirroring app/compliance/rules.py's RULES-list extension pattern
(never touching the dispatch loop in app/provider_events/service.py).

None of these event types can be produced by any real provider today — no
Android Pub/Sub subscriber, no Apple MDM check-in endpoint exist in this
codebase (see docs/architecture/provider-events.md). This module exists so
the handler architecture is ready and unit-testable ahead of that real
integration, not because anything currently calls it from live traffic.

Every handler that touches a DeviceCommand goes through
CommandService.apply_provider_event, never a raw repository update — that
method is the one place "never mark a command COMPLETED merely because a
provider event exists" is enforced (SENT-only, terminal-only transitions,
resolved by provider_command_id within the event's own organization).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommandStatus
from app.commands.service import CommandService
from app.core.errors import AppError
from app.jobs.service import JobService
from app.platform.registry import PlatformProviderRegistry
from app.provider_events.models import ProviderEvent
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

# Event type string constants — matched against ProviderEvent.event_type by
# value (see that column's comment for why it's a free-form string, not an
# enum: no real payload shape exists yet to validate against).
EVENT_TYPE_COMMAND_COMPLETED = "COMMAND_COMPLETED"
EVENT_TYPE_COMMAND_FAILED = "COMMAND_FAILED"
EVENT_TYPE_COMMAND_ACKNOWLEDGED = "COMMAND_ACKNOWLEDGED"
EVENT_TYPE_DEVICE_CHECK_IN = "DEVICE_CHECK_IN"
EVENT_TYPE_DEVICE_UPDATED = "DEVICE_UPDATED"

EventHandler = Callable[[AsyncSession, ProviderEvent], Awaitable[None]]


def _unresolved_organization_error() -> AppError:
    return AppError(
        code="PROVIDER_EVENT_ORGANIZATION_UNRESOLVED",
        message="This event has no resolved organization_id and cannot be "
        "bridged to a command or device — see docs/architecture/provider-events.md §3.",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


def _require_provider_command_id(event: ProviderEvent) -> str:
    provider_command_id = event.payload.get("provider_command_id")
    if not isinstance(provider_command_id, str) or not provider_command_id:
        raise AppError(
            code="PROVIDER_EVENT_MISSING_COMMAND_ID",
            message="Event payload is missing a string 'provider_command_id'.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return provider_command_id


def _command_service(session: AsyncSession) -> CommandService:
    return CommandService(
        DeviceCommandRepository(session),
        DeviceRepository(session),
        PlatformProviderRegistry(),
        AuditRepository(session),
        JobService(BackgroundJobRepository(session), AuditRepository(session)),
        ProviderDeviceLinkRepository(session),
    )


async def _handle_command_completed(session: AsyncSession, event: ProviderEvent) -> None:
    """Never mark a command COMPLETED merely because this event exists — the
    transition is only ever applied through apply_provider_event, which
    itself only accepts SENT -> terminal (Phase 8 §28)."""
    if event.organization_id is None:
        raise _unresolved_organization_error()
    provider_command_id = _require_provider_command_id(event)
    result = event.payload.get("result")
    await _command_service(session).apply_provider_event(
        event.organization_id,
        provider_command_id=provider_command_id,
        new_status=DeviceCommandStatus.COMPLETED,
        result=result if isinstance(result, dict) else None,
        error_code=None,
        error_message=None,
    )


async def _handle_command_failed(session: AsyncSession, event: ProviderEvent) -> None:
    if event.organization_id is None:
        raise _unresolved_organization_error()
    provider_command_id = _require_provider_command_id(event)
    await _command_service(session).apply_provider_event(
        event.organization_id,
        provider_command_id=provider_command_id,
        new_status=DeviceCommandStatus.FAILED,
        result=None,
        error_code=str(event.payload.get("error_code") or "PROVIDER_REPORTED_FAILURE"),
        error_message=str(
            event.payload.get("error_message") or "The provider reported this command failed."
        ),
    )


async def _handle_command_acknowledged(session: AsyncSession, event: ProviderEvent) -> None:
    """Phase 6 deliberately has no ACKNOWLEDGED DeviceCommandStatus — 'sent
    but not yet known to have executed' already IS the SENT state, and
    inventing a new status for 'the device saw it', with no real transition
    behind it, would violate the state-machine-preservation rule (Phase 8
    §17). This handler exists so the event is still recognized
    (processing_status=PROCESSED, not IGNORED) and visible via the
    provider-event admin API — it deliberately makes no DeviceCommand
    change, only validates the payload shape so a malformed one still
    surfaces as a processing failure rather than being silently accepted."""
    if event.organization_id is None:
        raise _unresolved_organization_error()
    _require_provider_command_id(event)


async def _handle_device_check_in(session: AsyncSession, event: ProviderEvent) -> None:
    """Treated exactly like the existing device heartbeat trigger (Phase
    7): schedule a compliance re-evaluation rather than evaluating inline.
    Requires a resolved device — see app/provider_events/service.py."""
    if event.organization_id is None or event.device_id is None:
        raise _unresolved_organization_error()
    job_service = JobService(BackgroundJobRepository(session), AuditRepository(session))
    await job_service.enqueue_compliance_evaluation(event.organization_id, event.device_id)


async def _handle_device_updated(session: AsyncSession, event: ProviderEvent) -> None:
    """Mirrors CommandService._dispatch's REFRESH_INFO device-field sync —
    only known, non-sensitive fields are ever written from an external
    payload, never an arbitrary key from it."""
    if event.organization_id is None or event.device_id is None:
        raise _unresolved_organization_error()
    device_repo = DeviceRepository(session)
    device = await device_repo.get_for_organization(event.organization_id, event.device_id)
    if device is None:
        raise AppError(
            code="DEVICE_NOT_FOUND",
            message="The resolved device no longer exists.",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    fields = {
        key: value
        for key, value in event.payload.items()
        if key in ("os_version", "client_version")
    }
    if fields:
        await device_repo.update_for_organization(device, **fields)


_HANDLERS: dict[str, EventHandler] = {
    EVENT_TYPE_COMMAND_COMPLETED: _handle_command_completed,
    EVENT_TYPE_COMMAND_FAILED: _handle_command_failed,
    EVENT_TYPE_COMMAND_ACKNOWLEDGED: _handle_command_acknowledged,
    EVENT_TYPE_DEVICE_CHECK_IN: _handle_device_check_in,
    EVENT_TYPE_DEVICE_UPDATED: _handle_device_updated,
}


def get_handler(event_type: str) -> EventHandler | None:
    return _HANDLERS.get(event_type)
