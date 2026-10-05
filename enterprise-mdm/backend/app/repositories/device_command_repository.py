"""DeviceCommand repository — the ONLY layer that constructs SQLAlchemy
queries for DeviceCommand. Tenant-isolation rule as in
app/repositories/device_repository.py: every method requires
organization_id and filters on it — there is no bare `get_by_id`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.models import DeviceCommand, DeviceCommandStatus


class DeviceCommandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_query(self, organization_id: uuid.UUID) -> Select[tuple[DeviceCommand]]:
        return select(DeviceCommand).where(DeviceCommand.organization_id == organization_id)

    async def get_for_organization(
        self, organization_id: uuid.UUID, command_id: uuid.UUID
    ) -> DeviceCommand | None:
        result = await self._session.execute(
            self._base_query(organization_id).where(DeviceCommand.id == command_id)
        )
        return result.scalar_one_or_none()

    async def get_by_device_and_id(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, command_id: uuid.UUID
    ) -> DeviceCommand | None:
        result = await self._session.execute(
            self._base_query(organization_id).where(
                DeviceCommand.device_id == device_id, DeviceCommand.id == command_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_provider_command_id(
        self, organization_id: uuid.UUID, provider_command_id: str
    ) -> DeviceCommand | None:
        """Used by app/provider_events/handlers.py to bridge an inbound
        provider event back to the command it reports on — org-scoped like
        every other lookup here, so an event resolved to the wrong
        organization can never reach a foreign command."""
        result = await self._session.execute(
            self._base_query(organization_id).where(
                DeviceCommand.provider_command_id == provider_command_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, idempotency_key: str
    ) -> DeviceCommand | None:
        result = await self._session.execute(
            self._base_query(organization_id).where(
                DeviceCommand.device_id == device_id,
                DeviceCommand.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_device(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        status: DeviceCommandStatus | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> Sequence[DeviceCommand]:
        query = self._base_query(organization_id).where(DeviceCommand.device_id == device_id)
        if status is not None:
            query = query.where(DeviceCommand.status == status)
        query = query.order_by(DeviceCommand.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(query)
        return result.scalars().all()

    async def count_for_device(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        status: DeviceCommandStatus | None = None,
    ) -> int:
        query = (
            select(func.count())
            .select_from(DeviceCommand)
            .where(
                DeviceCommand.organization_id == organization_id,
                DeviceCommand.device_id == device_id,
            )
        )
        if status is not None:
            query = query.where(DeviceCommand.status == status)
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def list_expired_sent_unscoped(
        self, *, before: datetime, limit: int = 500
    ) -> Sequence[DeviceCommand]:
        """System-only sweep across every organization, used exclusively by
        CommandService.reconcile_expired_sent_commands (the Phase 7 worker
        loop) — the one legitimate cross-tenant query on this repository,
        mirroring DeviceRepository.get_by_id_unscoped's precedent. Never
        call this from an admin-facing code path."""
        result = await self._session.execute(
            select(DeviceCommand)
            .where(
                DeviceCommand.status == DeviceCommandStatus.SENT,
                DeviceCommand.expires_at <= before,
            )
            .order_by(DeviceCommand.expires_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def create(self, organization_id: uuid.UUID, **fields: object) -> DeviceCommand:
        command = DeviceCommand(organization_id=organization_id, **fields)
        self._session.add(command)
        await self._session.flush()
        await self._session.refresh(command)
        return command

    async def update_for_organization(
        self, command: DeviceCommand, **fields: object
    ) -> DeviceCommand:
        for key, value in fields.items():
            setattr(command, key, value)
        await self._session.flush()
        await self._session.refresh(command)
        return command

    async def rollback(self) -> None:
        """Recover the session after a caught IntegrityError (e.g. a
        concurrent idempotency-key race — see CommandService.create_command)
        so it can keep being used for a follow-up SELECT. A session that
        just failed a flush cannot run further statements until rolled
        back."""
        await self._session.rollback()
