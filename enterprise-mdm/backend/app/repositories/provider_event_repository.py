"""ProviderEvent repository — the ONLY layer that constructs SQLAlchemy
queries for this model."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.provider_events.models import ProviderEvent, ProviderEventProcessingStatus


class ProviderEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_provider_event_id(
        self, provider_name: str, provider_event_id: str
    ) -> ProviderEvent | None:
        result = await self._session.execute(
            select(ProviderEvent).where(
                ProviderEvent.provider_name == provider_name,
                ProviderEvent.provider_event_id == provider_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_for_organization(
        self, organization_id: uuid.UUID, event_id: uuid.UUID
    ) -> ProviderEvent | None:
        """Admin-API read — org-scoped like every other Phase 4+ resource.
        Deliberately cannot return an event with organization_id=NULL: an
        unresolved event has no organization to be scoped by, so it is
        simply invisible through this tenant-facing lookup (see
        docs/architecture/provider-events.md §5 for why that's an accepted
        Phase 8 boundary, not an oversight)."""
        result = await self._session.execute(
            select(ProviderEvent).where(
                ProviderEvent.id == event_id,
                ProviderEvent.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        provider_name: str | None = None,
        event_type: str | None = None,
        processing_status: ProviderEventProcessingStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ProviderEvent]:
        query = select(ProviderEvent).where(ProviderEvent.organization_id == organization_id)
        if provider_name is not None:
            query = query.where(ProviderEvent.provider_name == provider_name)
        if event_type is not None:
            query = query.where(ProviderEvent.event_type == event_type)
        if processing_status is not None:
            query = query.where(ProviderEvent.processing_status == processing_status)
        result = await self._session.execute(
            query.order_by(ProviderEvent.received_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        provider_name: str | None = None,
        event_type: str | None = None,
        processing_status: ProviderEventProcessingStatus | None = None,
    ) -> int:
        query = (
            select(func.count())
            .select_from(ProviderEvent)
            .where(ProviderEvent.organization_id == organization_id)
        )
        if provider_name is not None:
            query = query.where(ProviderEvent.provider_name == provider_name)
        if event_type is not None:
            query = query.where(ProviderEvent.event_type == event_type)
        if processing_status is not None:
            query = query.where(ProviderEvent.processing_status == processing_status)
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def create(self, **fields: object) -> ProviderEvent:
        event = ProviderEvent(**fields)
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def mark_processed(
        self,
        event: ProviderEvent,
        processing_status: ProviderEventProcessingStatus,
        *,
        error: str | None,
    ) -> ProviderEvent:
        event.processing_status = processing_status
        event.processed_at = datetime.now(tz=UTC)
        event.processing_error = error[:2000] if error else None
        await self._session.flush()
        await self._session.refresh(event)
        return event
