"""Device group service — CRUD and membership management.

Constructs no raw SQL/ORM queries — delegates strictly to
DeviceGroupRepository and DeviceRepository (to verify a device belongs to
the same organization before allowing it into a group — STEP 11).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.device_groups.models import DeviceGroup
from app.devices.models import Device
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository


def _not_found() -> AppError:
    return AppError(
        code="DEVICE_GROUP_NOT_FOUND",
        message="Device group not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


class DeviceGroupService:
    def __init__(
        self,
        group_repo: DeviceGroupRepository,
        device_repo: DeviceRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._group_repo = group_repo
        self._device_repo = device_repo
        self._audit_repo = audit_repo

    async def get_group(self, organization_id: uuid.UUID, group_id: uuid.UUID) -> DeviceGroup:
        group = await self._group_repo.get_for_organization(organization_id, group_id)
        if group is None:
            raise _not_found()
        return group

    async def list_groups(
        self, organization_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[DeviceGroup], int]:
        groups = await self._group_repo.list_for_organization(
            organization_id, offset=offset, limit=limit
        )
        total = await self._group_repo.count_for_organization(organization_id)
        return groups, total

    async def create_group(
        self,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        name: str,
        description: str | None,
    ) -> DeviceGroup:
        if await self._group_repo.exists_by_name(organization_id, name):
            raise AppError(
                code="DEVICE_GROUP_ALREADY_EXISTS",
                message="A device group with this name already exists in this organization.",
                status_code=status.HTTP_409_CONFLICT,
            )
        group = await self._group_repo.create(
            organization_id, name=name, description=description
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_GROUP_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"group_id": str(group.id), "name": name},
        )
        return group

    async def update_group(
        self,
        organization_id: uuid.UUID,
        group_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        name: str | None,
        description: str | None,
    ) -> DeviceGroup:
        group = await self.get_group(organization_id, group_id)
        clean_fields = {
            k: v
            for k, v in {"name": name, "description": description}.items()
            if v is not None
        }
        if not clean_fields:
            return group
        new_name = clean_fields.get("name")
        if isinstance(new_name, str) and new_name != group.name:
            if await self._group_repo.exists_by_name(organization_id, new_name):
                raise AppError(
                    code="DEVICE_GROUP_ALREADY_EXISTS",
                    message="A device group with this name already exists in this organization.",
                    status_code=status.HTTP_409_CONFLICT,
                )
        group = await self._group_repo.update_for_organization(group, **clean_fields)
        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_GROUP_UPDATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"group_id": str(group.id), "fields": list(clean_fields.keys())},
        )
        return group

    async def delete_group(
        self, organization_id: uuid.UUID, group_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> None:
        group = await self.get_group(organization_id, group_id)
        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_GROUP_DELETED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"group_id": str(group.id), "name": group.name},
        )
        await self._group_repo.delete(group)

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    async def add_device(
        self,
        organization_id: uuid.UUID,
        group_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> None:
        group = await self.get_group(organization_id, group_id)
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if await self._group_repo.is_member(group.id, device.id):
            return
        await self._group_repo.add_member(group.id, device.id)
        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_GROUP_MEMBER_ADDED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"group_id": str(group.id), "device_id": str(device.id)},
        )

    async def remove_device(
        self,
        organization_id: uuid.UUID,
        group_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
    ) -> None:
        group = await self.get_group(organization_id, group_id)
        await self._group_repo.remove_member(group.id, device_id)
        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_GROUP_MEMBER_REMOVED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"group_id": str(group.id), "device_id": str(device_id)},
        )

    async def list_devices(
        self, organization_id: uuid.UUID, group_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> tuple[Sequence[Device], int]:
        group = await self.get_group(organization_id, group_id)
        devices = await self._group_repo.list_devices_for_group(
            group.id, offset=offset, limit=limit
        )
        total = await self._group_repo.count_devices_for_group(group.id)
        return devices, total

    async def device_count(self, group_id: uuid.UUID) -> int:
        return await self._group_repo.count_devices_for_group(group_id)
