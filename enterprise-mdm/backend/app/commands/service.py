"""Remote device command service — validation, dispatch through a
PlatformManagementProvider, and lifecycle management for DeviceCommand.

Constructs no raw SQL/ORM queries — delegates strictly to
DeviceCommandRepository, DeviceRepository, PlatformProviderRegistry, and
AuditRepository. See docs/architecture/device-commands.md for the full
state machine and idempotency/expiration design, and
docs/architecture/background-jobs.md §7 for the Phase 8 dispatch flow.

Dispatch happens off the issuing HTTP request as of Phase 8: `create_command`
enqueues a COMMAND_DISPATCH background job and returns the command still
PENDING; `app/workers/loop.py` claims that job and calls
`process_dispatch_job`, which reuses `_dispatch` unchanged. Before Phase 8,
dispatch happened synchronously inline — that meant a slow/unreachable
provider blocked the issuing request, and it's why Phase 6 commands could
never observably reach a real "the request is still in flight" state; the
worker queue removes that constraint. See
ProviderCommandStatus's docstring in app/platform/base/provider.py for why
this still means real providers can only ever report SENT, not COMPLETED,
for a genuinely asynchronous platform command — that hasn't changed.

Command expiration is evaluated lazily, exactly like Phase 5's enrollment
tokens (`app/enrollment/service.py::_effective_token_status`) and device
connectivity (`app/devices/connectivity.py`) — no background job flips
SENT -> EXPIRED on a timer (that's `reconcile_expired_sent_commands`
proactively, plus this lazy fallback); `_effective_command_status` computes
it at read time and persists the transition if it has changed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError

from app.audit.models import AuditEventType
from app.commands.models import DeviceCommand, DeviceCommandStatus, DeviceCommandType
from app.core.config import get_settings
from app.core.errors import AppError
from app.devices.enums import DevicePlatform, EnrollmentStatus
from app.devices.models import Device
from app.jobs.service import JobService
from app.platform.base.capabilities import PlatformCapability
from app.platform.base.errors import PlatformProviderUnavailableError
from app.platform.base.provider import (
    PlatformManagementProvider,
    ProviderCommandRequest,
    ProviderCommandResult,
    ProviderDeviceRef,
)
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

_CAPABILITY_BY_TYPE: dict[DeviceCommandType, PlatformCapability] = {
    DeviceCommandType.LOCK: PlatformCapability.LOCK,
    DeviceCommandType.REBOOT: PlatformCapability.REBOOT,
    DeviceCommandType.WIPE: PlatformCapability.WIPE,
    DeviceCommandType.REFRESH_INFO: PlatformCapability.REFRESH_INFO,
    DeviceCommandType.POLICY_SYNC: PlatformCapability.POLICY_SYNC,
    DeviceCommandType.UNENROLL: PlatformCapability.UNENROLL,
    DeviceCommandType.INSTALL_APPLICATION: PlatformCapability.APPLICATION_INSTALL,
    DeviceCommandType.UNINSTALL_APPLICATION: PlatformCapability.APPLICATION_REMOVE,
}

_TERMINAL_STATUSES = frozenset(
    {
        DeviceCommandStatus.COMPLETED,
        DeviceCommandStatus.FAILED,
        DeviceCommandStatus.CANCELLED,
        DeviceCommandStatus.EXPIRED,
    }
)


def _effective_command_status(command: DeviceCommand) -> DeviceCommandStatus:
    if command.status in _TERMINAL_STATUSES:
        return command.status
    if command.status == DeviceCommandStatus.SENT:
        # SQLite (test harness) drops tzinfo on round-trip even for
        # DateTime(timezone=True) columns; Postgres does not. Normalize to
        # UTC before comparing, matching the established fix in
        # app/auth/models.py::Session.is_active and
        # app/enrollment/service.py::_effective_token_status.
        expires_at = command.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if datetime.now(tz=UTC) >= expires_at:
            return DeviceCommandStatus.EXPIRED
    return command.status


def _require_capability_supported(
    provider: PlatformManagementProvider, command_type: DeviceCommandType
) -> None:
    """Phase 12: pure, no-I/O check shared by `create_command` (synchronous,
    at request time — so an obviously-unsupported command is rejected
    immediately rather than silently queued for a background job to
    discover and fail asynchronously, per the capability-gate requirement)
    and `_dispatch` (kept as defense in depth, e.g. against
    platform_provider_mode changing between creation and dispatch)."""
    capability = _CAPABILITY_BY_TYPE[command_type]
    if capability not in provider.supported_capabilities:
        raise AppError(
            code="COMMAND_TYPE_NOT_SUPPORTED",
            message=f"The {provider.name} provider does not support "
            f"{command_type.value} for this device's platform.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )


def _expected_provider_name(platform_provider_mode: str, platform: DevicePlatform) -> str:
    """A pure, non-failing guess at which provider a command will attempt
    to use — populates the NOT NULL `provider_name` column at command
    creation time, before provider resolution (which can itself fail) has
    even been attempted. See the comment in `create_command` for why
    resolution is deferred to `_dispatch`."""
    if platform_provider_mode == "mock":
        return "mock"
    if platform == DevicePlatform.ANDROID:
        return "android"
    if platform in (DevicePlatform.IOS, DevicePlatform.IPADOS):
        return "apple"
    return "unsupported"


async def _call_provider(
    provider: PlatformManagementProvider,
    command_type: DeviceCommandType,
    request: ProviderCommandRequest,
) -> ProviderCommandResult:
    if command_type == DeviceCommandType.LOCK:
        return await provider.lock_device(request)
    if command_type == DeviceCommandType.REBOOT:
        return await provider.restart_device(request)
    if command_type == DeviceCommandType.WIPE:
        return await provider.wipe_device(request)
    if command_type == DeviceCommandType.REFRESH_INFO:
        return await provider.get_device(request)
    if command_type == DeviceCommandType.POLICY_SYNC:
        return await provider.apply_policy(request)
    if command_type == DeviceCommandType.UNENROLL:
        return await provider.unenroll_device(request)
    if command_type == DeviceCommandType.INSTALL_APPLICATION:
        return await provider.install_application(request)
    if command_type == DeviceCommandType.UNINSTALL_APPLICATION:
        return await provider.remove_application(request)
    raise AssertionError(f"Unhandled DeviceCommandType: {command_type!r}")  # pragma: no cover


class CommandService:
    def __init__(
        self,
        command_repo: DeviceCommandRepository,
        device_repo: DeviceRepository,
        provider_registry: PlatformProviderRegistry,
        audit_repo: AuditRepository,
        job_service: JobService,
        provider_device_link_repo: ProviderDeviceLinkRepository,
    ) -> None:
        self._command_repo = command_repo
        self._device_repo = device_repo
        self._provider_registry = provider_registry
        self._audit_repo = audit_repo
        self._job_service = job_service
        self._provider_device_link_repo = provider_device_link_repo

    async def _get_device_or_404(self, organization_id: uuid.UUID, device_id: uuid.UUID) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return device

    # ------------------------------------------------------------------
    # Create + dispatch
    # ------------------------------------------------------------------

    async def create_command(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        command_type: DeviceCommandType,
        payload: dict[str, Any] | None,
        idempotency_key: str | None,
        actor_user_id: uuid.UUID | None,
    ) -> DeviceCommand:
        """`actor_user_id=None` is for a genuinely system-initiated command
        (Phase 13's automated compliance remediation) — `DeviceCommand.requested_by`
        has been nullable and documented as "reserved for a future
        system-initiated command" since Phase 6; this is that future.
        Every admin-facing router still always passes a real user id."""
        device = await self._get_device_or_404(organization_id, device_id)

        if device.enrollment_status != EnrollmentStatus.ENROLLED:
            raise AppError(
                code="DEVICE_NOT_ENROLLED",
                message="Cannot issue a remote command to a device that is not enrolled.",
                status_code=status.HTTP_409_CONFLICT,
            )

        if idempotency_key:
            existing = await self._command_repo.get_by_idempotency_key(
                organization_id, device_id, idempotency_key
            )
            if existing is not None:
                # Same key seen before for this org+device: return the
                # existing command unchanged rather than dispatching again
                # — this is the whole point of the key (STEP 7: a retried
                # request must never double-dispatch, especially wipe).
                return existing

        # Phase 12: reject an obviously-unsupported command type (e.g.
        # REBOOT against an Apple device) before ever creating a row —
        # but ONLY when the provider itself resolves successfully. This is
        # a deterministic, permanent fact about the platform, unlike
        # "provider unavailable" below, so it's safe and correct to fail
        # fast here rather than silently queuing a command that could
        # never have succeeded (brief: "do not silently accept unsupported
        # commands"). Resolving the provider here is cheap — no network
        # I/O, just a settings read and object construction — so this
        # stays consistent with "do not run expensive work synchronously."
        try:
            provider_for_capability_check = self._provider_registry.get_provider(device.platform)
        except PlatformProviderUnavailableError:
            provider_for_capability_check = None
        if provider_for_capability_check is not None:
            _require_capability_supported(provider_for_capability_check, command_type)

        # The provider is NOT resolved again below for the "unavailable"
        # case. Whether a provider is even reachable/configured is itself
        # something that can fail (a real provider's constructor raises
        # PlatformProviderUnavailableError when uncredentialed) — that
        # failure must still produce a persisted, audited FAILED command
        # row rather than an unhandled 503 with no record an attempt was
        # ever made. So that resolution happens inside `_dispatch`, after
        # the row already exists, wrapped in the same try/except as the
        # dispatch call itself. `expected_provider_name` is a pure, non-failing guess
        # used only to populate the NOT NULL provider_name column at
        # creation time; `_dispatch` may later find the real provider
        # differs (it never does today, but nothing depends on equality).
        # Mode comes from THIS SERVICE'S OWN injected registry, never the
        # ambient global get_settings() — see
        # PlatformProviderRegistry.platform_provider_mode's docstring for
        # the bug this fixed.
        expected_provider_name = _expected_provider_name(
            self._provider_registry.platform_provider_mode, device.platform
        )

        now = datetime.now(tz=UTC)
        expires_at = now + timedelta(hours=get_settings().device_command_expire_hours)

        try:
            command = await self._command_repo.create(
                organization_id,
                device_id=device_id,
                command_type=command_type,
                status=DeviceCommandStatus.PENDING,
                payload=payload,
                idempotency_key=idempotency_key,
                provider_name=expected_provider_name,
                requested_by=actor_user_id,
                expires_at=expires_at,
            )
        except IntegrityError:
            # A concurrent request raced us on the same (organization_id,
            # device_id, idempotency_key) unique constraint between our
            # SELECT above and this INSERT — the SELECT-then-INSERT check
            # is not atomic. Recover the session and return the row the
            # other request just created rather than dispatching twice.
            # Known, accepted race window — see
            # docs/architecture/device-commands.md.
            await self._command_repo.rollback()
            if idempotency_key:
                existing = await self._command_repo.get_by_idempotency_key(
                    organization_id, device_id, idempotency_key
                )
                if existing is not None:
                    return existing
            raise

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_COMMAND_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "command_id": str(command.id),
                "device_id": str(device_id),
                "command_type": command_type.value,
            },
        )

        # Phase 8: dispatch is enqueued, not called inline — the issuing
        # request must not block on a possibly slow/unreachable provider.
        # dedup_key is the command's own id, so a retried enqueue (there
        # isn't one on this path today, but see process_dispatch_job) can
        # never produce two active dispatch attempts for one command.
        await self._job_service.enqueue_command_dispatch(organization_id, command.id)
        return command

    # ------------------------------------------------------------------
    # Dispatch (Phase 8: invoked by the worker via process_dispatch_job,
    # never inline within create_command — see the module docstring)
    # ------------------------------------------------------------------

    async def process_dispatch_job(
        self, organization_id: uuid.UUID, command_id: uuid.UUID
    ) -> DeviceCommand:
        """Called by app/workers/loop.py for a claimed COMMAND_DISPATCH
        job. Attributes resulting audit events to the command's original
        `requested_by` — the job itself has no human actor, but the
        dispatch is still fundamentally that user's action, just executed
        asynchronously.

        Idempotency: if the command is no longer PENDING (a prior attempt
        of this job already dispatched it and committed), this is a no-op
        that returns the command unchanged rather than dispatching again —
        the one case this can't fully close is a worker crash strictly
        between a successful provider call and the commit that would have
        recorded it; see docs/architecture/background-jobs.md §7 for why
        that residual window is accepted rather than solved."""
        command = await self._command_repo.get_for_organization(organization_id, command_id)
        if command is None:
            raise AppError(
                code="COMMAND_NOT_FOUND",
                message="Command not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if command.status != DeviceCommandStatus.PENDING:
            return command

        device = await self._device_repo.get_for_organization(organization_id, command.device_id)
        if device is None:
            now = datetime.now(tz=UTC)
            command = await self._command_repo.update_for_organization(
                command,
                status=DeviceCommandStatus.FAILED,
                dispatched_at=now,
                completed_at=now,
                error_code="DEVICE_NOT_FOUND",
                error_message="The device no longer exists.",
            )
            await self._audit_repo.log_event(
                event_type=AuditEventType.DEVICE_COMMAND_FAILED,
                actor_user_id=command.requested_by,
                organization_id=organization_id,
                metadata={
                    "command_id": str(command.id),
                    "command_type": command.command_type.value,
                    "error_code": "DEVICE_NOT_FOUND",
                },
            )
            return command

        return await self._dispatch(command, device, actor_user_id=command.requested_by)

    async def _dispatch(
        self,
        command: DeviceCommand,
        device: Device,
        *,
        actor_user_id: uuid.UUID | None,
    ) -> DeviceCommand:
        # Phase 9: resolve this device's real provider-native identifier
        # (if any) through the same ProviderDeviceLink table Phase 8 built
        # for the inbound direction — closing the loop so a real provider
        # actually receives something to address. Every provider still
        # fails fast with a clear error when this is None (see
        # app/platform/android/provider.py / apple/provider.py's own
        # _require_provider_device_id checks) — nothing here weakens that;
        # it only means a link *can* make a real dispatch possible once one
        # exists, which today it never does (no real enrollment produces
        # one — see docs/architecture/provider-events.md §4).
        #
        # Phase 10: Apple is a genuine special case here, not an
        # inconsistency to paper over — its outbound dispatch identifier
        # (the APNs push token) is a different value from
        # provider_device_id (the UDID, used for inbound check-in
        # resolution). See ProviderDeviceLink.apple_push_token's docstring.
        if command.provider_name == "apple":
            provider_device_id = await self._provider_device_link_repo.get_apple_push_token(
                command.organization_id, device.id
            )
        else:
            provider_device_id = await self._provider_device_link_repo.get_provider_device_id(
                command.organization_id, device.id, command.provider_name
            )
        request = ProviderCommandRequest(
            device=ProviderDeviceRef(
                device_id=device.id,
                platform=device.platform,
                provider_device_id=provider_device_id,
            ),
            payload=command.payload or {},
        )
        dispatched_at = datetime.now(tz=UTC)

        try:
            provider = self._provider_registry.get_provider(device.platform)
            _require_capability_supported(provider, command.command_type)
            result = await _call_provider(provider, command.command_type, request)
        except AppError as exc:
            # Covers COMMAND_TYPE_NOT_SUPPORTED (raised above) as well as
            # PlatformProviderUnavailableError / PlatformCommandRejectedError
            # (both AppError subclasses) raised by provider resolution or
            # the provider call itself — all three are "this specific
            # attempt failed," recorded identically as a terminal FAILED
            # command with a full audit trail, never an unhandled
            # exception that would leave no record an attempt was made.
            command = await self._command_repo.update_for_organization(
                command,
                status=DeviceCommandStatus.FAILED,
                dispatched_at=dispatched_at,
                completed_at=datetime.now(tz=UTC),
                error_code=exc.code,
                error_message=exc.message,
            )
            await self._audit_repo.log_event(
                event_type=AuditEventType.DEVICE_COMMAND_FAILED,
                actor_user_id=actor_user_id,
                organization_id=command.organization_id,
                metadata={
                    "command_id": str(command.id),
                    "command_type": command.command_type.value,
                    "error_code": exc.code,
                },
            )
            return command

        new_status = DeviceCommandStatus(result.status.value)
        is_terminal = new_status in _TERMINAL_STATUSES
        command = await self._command_repo.update_for_organization(
            command,
            status=new_status,
            dispatched_at=dispatched_at,
            completed_at=datetime.now(tz=UTC) if is_terminal else None,
            provider_command_id=result.provider_command_id,
            result=result.result,
            error_code=result.error_code,
            error_message=result.error_message,
        )

        if (
            command.command_type == DeviceCommandType.REFRESH_INFO
            and new_status == DeviceCommandStatus.COMPLETED
            and result.result
        ):
            device_fields = {
                key: value
                for key, value in result.result.items()
                if key in ("os_version", "client_version")
            }
            if device_fields:
                await self._device_repo.update_for_organization(device, **device_fields)

        if command.command_type == DeviceCommandType.WIPE and new_status in (
            DeviceCommandStatus.SENT,
            DeviceCommandStatus.COMPLETED,
        ):
            # A dispatched wipe is treated as an immediate credential
            # revocation event, not deferred to a completion signal this
            # codebase may never receive from a real provider — see
            # docs/security/phase-6-security-review.md.
            await self._device_repo.update_for_organization(device, device_secret_hash=None)

        if (
            command.command_type == DeviceCommandType.UNENROLL
            and new_status == DeviceCommandStatus.COMPLETED
        ):
            # Phase 12: unlike WIPE's proactive credential-clearing above
            # (triggered on SENT too), this waits for a genuine COMPLETED
            # confirmation — deleting the ProviderDeviceLink entirely is a
            # stronger, less-reversible action than clearing a credential,
            # so it's worth requiring real confirmation the provider
            # actually removed the device before this codebase forgets its
            # provider identity too. Mirrors exactly what
            # EnrollmentService.revoke_enrollment already does for an
            # admin-initiated revocation (see docs/architecture/phase-11.md
            # §5) — clear the heartbeat credential, mark REVOKED, and
            # remove the now-stale link so a future physical device
            # re-enrolling under the same provider identifier never
            # resolves back to this one.
            await self._device_repo.update_for_organization(
                device, device_secret_hash=None, enrollment_status=EnrollmentStatus.REVOKED
            )
            # Deliberately NOT get_link_for_device(..., command.provider_name):
            # command.provider_name is which provider actually DISPATCHED
            # this command (always "mock" under platform_provider_mode=
            # "mock", regardless of the device's real platform — see
            # _expected_provider_name) — a different concept from the
            # link's own provider_name (set at registration time from the
            # device's actual platform, e.g. "android"). Every other
            # dispatch-time lookup in this method keys on
            # command.provider_name because it's resolving *how to reach
            # the provider that is about to act* (correctly "mock" in mock
            # mode); this one is resolving *which link belongs to this
            # device*, an org+device-scoped question with no provider
            # ambiguity to begin with (get_any_link_for_device, exactly
            # like EnrollmentService.revoke_enrollment already uses).
            link = await self._provider_device_link_repo.get_any_link_for_device(
                command.organization_id, device.id
            )
            if link is not None:
                await self._provider_device_link_repo.delete(link)
                await self._audit_repo.log_event(
                    event_type=AuditEventType.PROVIDER_DEVICE_LINK_DELETED,
                    actor_user_id=actor_user_id,
                    organization_id=command.organization_id,
                    metadata={
                        "link_id": str(link.id),
                        "device_id": str(device.id),
                        "provider_name": link.provider_name,
                        "reason": "command_unenroll",
                    },
                )

        event_type = (
            AuditEventType.DEVICE_COMMAND_COMPLETED
            if new_status == DeviceCommandStatus.COMPLETED
            else AuditEventType.DEVICE_COMMAND_FAILED
            if new_status == DeviceCommandStatus.FAILED
            else AuditEventType.DEVICE_COMMAND_DISPATCHED
        )
        await self._audit_repo.log_event(
            event_type=event_type,
            actor_user_id=actor_user_id,
            organization_id=command.organization_id,
            metadata={
                "command_id": str(command.id),
                "command_type": command.command_type.value,
                "status": new_status.value,
            },
        )
        return command

    # ------------------------------------------------------------------
    # Capability discovery (Phase 12)
    # ------------------------------------------------------------------

    async def get_supported_command_types(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> tuple[str, bool, list[DeviceCommandType]]:
        """Returns (provider_name, provider_available, supported_command_types)
        for `GET /devices/{id}/commands/capabilities` — lets a caller (the
        frontend) know which command actions are even worth showing as
        executable BEFORE issuing one, rather than discovering
        unsupported-ness only after a rejected request. `provider_available
        =False` means the provider itself couldn't be constructed at all
        (e.g. real credentials missing) — in that case
        supported_command_types is empty, not a guess at what it might
        support if it were configured."""
        device = await self._get_device_or_404(organization_id, device_id)
        provider_name = _expected_provider_name(
            self._provider_registry.platform_provider_mode, device.platform
        )
        try:
            provider = self._provider_registry.get_provider(device.platform)
        except PlatformProviderUnavailableError:
            return provider_name, False, []
        supported = [
            command_type
            for command_type, capability in _CAPABILITY_BY_TYPE.items()
            if capability in provider.supported_capabilities
        ]
        return provider.name, True, supported

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def _apply_effective_status(self, command: DeviceCommand) -> DeviceCommand:
        effective = _effective_command_status(command)
        if effective != command.status:
            command = await self._command_repo.update_for_organization(
                command, status=effective, completed_at=datetime.now(tz=UTC)
            )
            await self._audit_repo.log_event(
                event_type=AuditEventType.DEVICE_COMMAND_EXPIRED,
                organization_id=command.organization_id,
                metadata={"command_id": str(command.id)},
            )
        return command

    async def get_command(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, command_id: uuid.UUID
    ) -> DeviceCommand:
        command = await self._command_repo.get_by_device_and_id(
            organization_id, device_id, command_id
        )
        if command is None:
            raise AppError(
                code="COMMAND_NOT_FOUND",
                message="Command not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return await self._apply_effective_status(command)

    async def list_commands(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        status_filter: DeviceCommandStatus | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[DeviceCommand], int]:
        await self._get_device_or_404(organization_id, device_id)
        commands = await self._command_repo.list_for_device(
            organization_id, device_id, status=status_filter, offset=offset, limit=limit
        )
        refreshed = [await self._apply_effective_status(c) for c in commands]
        total = await self._command_repo.count_for_device(
            organization_id, device_id, status=status_filter
        )
        return refreshed, total

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    async def cancel_command(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        command_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> DeviceCommand:
        """Legal only from SENT. NOTE: for a real provider this only stops
        server-side tracking of the command — it can never actually recall
        a push that Android/Apple's platform has already accepted; there is
        no cancel-in-flight API surfaced by either platform's command
        model. Mock-provider commands never observably reach SENT by
        default (they resolve straight to COMPLETED/FAILED) — reaching
        SENT against Mock requires the test-only `simulate_sent` payload
        key (see MockManagementProvider), deliberately not exposed through
        any public request schema."""
        command = await self.get_command(organization_id, device_id, command_id)
        if command.status != DeviceCommandStatus.SENT:
            raise AppError(
                code="INVALID_COMMAND_TRANSITION",
                message=f"Cannot cancel a command in status {command.status.value}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        now = datetime.now(tz=UTC)
        command = await self._command_repo.update_for_organization(
            command,
            status=DeviceCommandStatus.CANCELLED,
            cancelled_by=actor_user_id,
            cancelled_at=now,
            completed_at=now,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_COMMAND_CANCELLED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"command_id": str(command.id)},
        )
        return command

    # ------------------------------------------------------------------
    # Provider-event bridge (Phase 8)
    # ------------------------------------------------------------------

    async def apply_provider_event(
        self,
        organization_id: uuid.UUID,
        *,
        provider_command_id: str,
        new_status: DeviceCommandStatus,
        result: dict[str, Any] | None,
        error_code: str | None,
        error_message: str | None,
    ) -> DeviceCommand:
        """Bridges an inbound ProviderEvent to a command state transition —
        called by app/provider_events/handlers.py, never directly by a
        router. Only legal from SENT to a terminal status: a command a
        provider was never told about, or one already terminal, can never
        be legitimately advanced by an external event. This is the control
        point for "never mark a command COMPLETED merely because a
        provider event exists" — the event is trusted only to report a
        transition on a command it can specifically, uniquely identify by
        `provider_command_id` within the caller's own organization; it
        cannot invent a command, resurrect a terminal one, or skip PENDING
        straight to a result."""
        command = await self._command_repo.get_by_provider_command_id(
            organization_id, provider_command_id
        )
        if command is None:
            raise AppError(
                code="COMMAND_NOT_FOUND",
                message="No command in this organization matches that provider_command_id.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if command.status != DeviceCommandStatus.SENT:
            raise AppError(
                code="INVALID_COMMAND_TRANSITION",
                message=f"Cannot apply a provider event to a command in status "
                f"{command.status.value}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if new_status not in _TERMINAL_STATUSES:
            raise AppError(
                code="INVALID_PROVIDER_EVENT_STATUS",
                message="A provider event may only drive a command to a terminal status.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        now = datetime.now(tz=UTC)
        command = await self._command_repo.update_for_organization(
            command,
            status=new_status,
            completed_at=now,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )

        if (
            command.command_type == DeviceCommandType.REFRESH_INFO
            and new_status == DeviceCommandStatus.COMPLETED
            and result
        ):
            device_fields = {
                key: value
                for key, value in result.items()
                if key in ("os_version", "client_version")
            }
            if device_fields:
                device = await self._device_repo.get_for_organization(
                    organization_id, command.device_id
                )
                if device is not None:
                    await self._device_repo.update_for_organization(device, **device_fields)

        event_type = (
            AuditEventType.DEVICE_COMMAND_COMPLETED
            if new_status == DeviceCommandStatus.COMPLETED
            else AuditEventType.DEVICE_COMMAND_FAILED
        )
        await self._audit_repo.log_event(
            event_type=event_type,
            actor_user_id=command.requested_by,
            organization_id=organization_id,
            metadata={
                "command_id": str(command.id),
                "command_type": command.command_type.value,
                "status": new_status.value,
                "source": "provider_event",
            },
        )
        return command

    # ------------------------------------------------------------------
    # Reconciliation (Phase 7)
    # ------------------------------------------------------------------

    async def reconcile_expired_sent_commands(self, *, batch_size: int = 500) -> int:
        """Plain sweep function, not a background_jobs row — a sweep over
        every organization has no natural single subject/dedup key and is
        trivially self-healing (a command left over from a missed cycle is
        still exactly as expired next cycle), so forcing it into the
        one-job-one-outcome BackgroundJob shape was judged the wrong fit.
        Called directly by app/workers/loop.py each cycle.

        Reuses `_effective_command_status`'s lazy-expiry logic unchanged —
        this only makes that same transition happen proactively, for
        commands nobody has read (and thus lazily transitioned) since they
        expired, instead of leaving them sitting in SENT forever with no
        completion-callback mechanism (see docs/architecture/device-commands.md §7).
        Returns the number of commands transitioned."""
        now = datetime.now(tz=UTC)
        commands = await self._command_repo.list_expired_sent_unscoped(
            before=now, limit=batch_size
        )
        for command in commands:
            command = await self._command_repo.update_for_organization(
                command, status=DeviceCommandStatus.EXPIRED, completed_at=now
            )
            await self._audit_repo.log_event(
                event_type=AuditEventType.DEVICE_COMMAND_EXPIRED,
                actor_user_id=None,
                organization_id=command.organization_id,
                metadata={"command_id": str(command.id)},
            )
        return len(commands)
