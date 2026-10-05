"""Provider-event ingestion + processing service (Phase 7, extended Phase 8
read, Phase 10 manual retry).

`ingest_event` is a callable service method only — no router. Real Android
Pub/Sub and Apple MDM check-in HTTP entry points exist as of Phase 9
(app/platform/android/router.py, app/platform/apple/router.py) and call
this directly.

Constructs no raw SQL/ORM queries — delegates strictly to
ProviderEventRepository, DeviceRepository, ProviderDeviceLinkRepository,
and AuditRepository. Read-only listing (list_for_organization/
get_for_organization) backs the Phase 8 admin API in
app/provider_events/router.py; `retry_event` backs its Phase 10 addition.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.provider_events.handlers import get_handler
from app.provider_events.models import ProviderEvent, ProviderEventProcessingStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.repositories.provider_event_repository import ProviderEventRepository


class ProviderEventService:
    def __init__(
        self,
        event_repo: ProviderEventRepository,
        device_repo: DeviceRepository,
        device_link_repo: ProviderDeviceLinkRepository,
        session: AsyncSession,
        audit_repo: AuditRepository,
    ) -> None:
        self._event_repo = event_repo
        self._device_repo = device_repo
        self._device_link_repo = device_link_repo
        self._session = session
        self._audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_event(
        self,
        *,
        provider_name: str,
        provider_event_id: str,
        event_type: str,
        payload: dict[str, Any],
        organization_id: uuid.UUID | None = None,
        provider_device_id: str | None = None,
    ) -> ProviderEvent:
        """Idempotent: redelivery of the same (provider_name,
        provider_event_id) pair returns the original row unchanged —
        neither device resolution nor event processing (§ below) ever runs
        twice for the same event."""
        existing = await self._event_repo.get_by_provider_event_id(
            provider_name, provider_event_id
        )
        if existing is not None:
            return existing

        resolved_device_id: uuid.UUID | None = None
        if organization_id is not None and provider_device_id is not None:
            resolved_device_id = await self._device_link_repo.get_device_id(
                organization_id, provider_name, provider_device_id
            )

        event = await self._event_repo.create(
            organization_id=organization_id,
            device_id=resolved_device_id,
            provider_name=provider_name,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=payload,
            received_at=datetime.now(tz=UTC),
            processing_status=ProviderEventProcessingStatus.PENDING,
        )
        return await self._process_event(event)

    async def _process_event(self, event: ProviderEvent) -> ProviderEvent:
        """Normalize -> identify event type -> handler -> domain service ->
        state change -> audit -> completion (the handler itself performs
        the domain-service/state-change/audit steps; this method only
        records the outcome). An event type with no registered handler is
        IGNORED, not FAILED — it is not an error for this system to not
        yet act on an event type, only for a recognized one to error out."""
        handler = get_handler(event.event_type)
        if handler is None:
            return await self._event_repo.mark_processed(
                event, ProviderEventProcessingStatus.IGNORED, error=None
            )
        try:
            await handler(self._session, event)
        except Exception as exc:  # noqa: BLE001 - a bad/malformed payload must never crash ingestion
            return await self._event_repo.mark_processed(
                event, ProviderEventProcessingStatus.FAILED, error=str(exc)
            )
        return await self._event_repo.mark_processed(
            event, ProviderEventProcessingStatus.PROCESSED, error=None
        )

    # ------------------------------------------------------------------
    # Read (Phase 8 admin API)
    # ------------------------------------------------------------------

    async def get_event(self, organization_id: uuid.UUID, event_id: uuid.UUID) -> ProviderEvent:
        event = await self._event_repo.get_for_organization(organization_id, event_id)
        if event is None:
            raise AppError(
                code="PROVIDER_EVENT_NOT_FOUND",
                message="Provider event not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return event

    async def list_events(
        self,
        organization_id: uuid.UUID,
        *,
        provider_name: str | None = None,
        event_type: str | None = None,
        processing_status: ProviderEventProcessingStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ProviderEvent], int]:
        events = await self._event_repo.list_for_organization(
            organization_id,
            provider_name=provider_name,
            event_type=event_type,
            processing_status=processing_status,
            offset=offset,
            limit=limit,
        )
        total = await self._event_repo.count_for_organization(
            organization_id,
            provider_name=provider_name,
            event_type=event_type,
            processing_status=processing_status,
        )
        return events, total

    # ------------------------------------------------------------------
    # Manual retry (Phase 10 admin API)
    # ------------------------------------------------------------------

    async def retry_event(
        self, organization_id: uuid.UUID, event_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> ProviderEvent:
        """Only a FAILED event may be manually retried — never PENDING
        (shouldn't exist post-ingestion; processing always runs inline),
        PROCESSED, or IGNORED. This re-runs `_process_event` directly —
        deliberately NOT `ingest_event` — so it cannot be confused with
        redelivery: the (provider_name, provider_event_id) idempotency
        check that protects against a *duplicate* event is a completely
        different guarantee from this explicit, single, admin-triggered
        re-attempt at processing the *same* row (Phase 10 §12: "never
        blindly process the same event twice" means never on ordinary
        redelivery, not that an operator can never explicitly ask for one
        more attempt at an event that's already known to have failed)."""
        event = await self.get_event(organization_id, event_id)
        if event.processing_status != ProviderEventProcessingStatus.FAILED:
            raise AppError(
                code="PROVIDER_EVENT_NOT_RETRYABLE",
                message=f"Only a FAILED event can be manually retried (this event is "
                f"{event.processing_status.value}).",
                status_code=status.HTTP_409_CONFLICT,
            )
        event = await self._process_event(event)
        await self._audit_repo.log_event(
            event_type=AuditEventType.PROVIDER_EVENT_RETRIED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "event_id": str(event.id),
                "event_type": event.event_type,
                "result": event.processing_status.value,
            },
        )
        return event
