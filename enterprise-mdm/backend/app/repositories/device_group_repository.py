"""DeviceGroup / DeviceGroupMember repository — the ONLY layer that
constructs SQLAlchemy queries for these models. Tenant-isolation rule as
elsewhere: every method requires organization_id and filters on it."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.device_groups.models import DeviceGroup, DeviceGroupMember
from app.devices.models import Device


class DeviceGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_organization(
        self, organization_id: uuid.UUID, group_id: uuid.UUID
    ) -> DeviceGroup | None:
        result = await self._session.execute(
            select(DeviceGroup).where(
                DeviceGroup.id == group_id, DeviceGroup.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self, organization_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> list[DeviceGroup]:
        result = await self._session.execute(
            select(DeviceGroup)
            .where(DeviceGroup.organization_id == organization_id)
            .order_by(DeviceGroup.name)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(self, organization_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(DeviceGroup)
            .where(DeviceGroup.organization_id == organization_id)
        )
        return int(result.scalar_one())

    async def exists_by_name(self, organization_id: uuid.UUID, name: str) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(DeviceGroup)
            .where(DeviceGroup.organization_id == organization_id, DeviceGroup.name == name)
        )
        return int(result.scalar_one()) > 0

    async def create(self, organization_id: uuid.UUID, **fields: object) -> DeviceGroup:
        group = DeviceGroup(organization_id=organization_id, **fields)
        self._session.add(group)
        await self._session.flush()
        await self._session.refresh(group)
        return group

    async def update_for_organization(self, group: DeviceGroup, **fields: object) -> DeviceGroup:
        for key, value in fields.items():
            setattr(group, key, value)
        await self._session.flush()
        await self._session.refresh(group)
        return group

    async def delete(self, group: DeviceGroup) -> None:
        await self._session.delete(group)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    async def is_member(self, group_id: uuid.UUID, device_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(DeviceGroupMember)
            .where(
                DeviceGroupMember.group_id == group_id,
                DeviceGroupMember.device_id == device_id,
            )
        )
        return int(result.scalar_one()) > 0

    async def add_member(self, group_id: uuid.UUID, device_id: uuid.UUID) -> None:
        self._session.add(DeviceGroupMember(group_id=group_id, device_id=device_id))
        await self._session.flush()

    async def remove_member(self, group_id: uuid.UUID, device_id: uuid.UUID) -> None:
        result = await self._session.execute(
            select(DeviceGroupMember).where(
                DeviceGroupMember.group_id == group_id,
                DeviceGroupMember.device_id == device_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is not None:
            await self._session.delete(member)
            await self._session.flush()

    async def list_devices_for_group(
        self, group_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> list[Device]:
        result = await self._session.execute(
            select(Device)
            .join(DeviceGroupMember, DeviceGroupMember.device_id == Device.id)
            .options(selectinload(Device.assigned_user))
            .where(DeviceGroupMember.group_id == group_id)
            .order_by(Device.display_name)
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_devices_for_group(self, group_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(DeviceGroupMember)
            .where(DeviceGroupMember.group_id == group_id)
        )
        return int(result.scalar_one())

    async def list_group_ids_for_device(self, device_id: uuid.UUID) -> list[uuid.UUID]:
        """Used by PolicyResolutionService to find every group a device
        belongs to, for device-group-scoped policy precedence."""
        result = await self._session.execute(
            select(DeviceGroupMember.group_id).where(DeviceGroupMember.device_id == device_id)
        )
        return list(result.scalars().all())
