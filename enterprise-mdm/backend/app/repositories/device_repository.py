"""Device repository — the ONLY layer that constructs SQLAlchemy queries for devices.

Tenant-isolation rule (matches app/repositories/membership_repository.py):
every method that touches a specific device takes `organization_id`
explicitly and filters on it — there is no `get_by_id(device_id)`. A device
belonging to a different organization resolves to "not found", never to a
cross-tenant leak.

Filtering, search, sorting, and pagination all happen in the database
(WHERE/LIMIT/OFFSET) — never "load everything then filter in Python" — so
this scales to the 10k-1M-device organizations Phase 4 is designed for.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.devices.enums import DevicePlatform, DeviceStatus, EnrollmentStatus, OwnershipType
from app.devices.models import Device
from app.provider_events.models import ProviderDeviceLink
from app.users.models import User

_RowT = TypeVar("_RowT", bound=tuple[object, ...])

# Whitelisted sort columns — never build ORDER BY from an unvalidated string.
_SORT_COLUMNS: dict[str, object] = {
    "created_at": Device.created_at,
    "updated_at": Device.updated_at,
    "display_name": Device.display_name,
    "last_seen_at": Device.last_seen_at,
    "status": Device.status,
    "platform": Device.platform,
}


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _base_query(self, organization_id: uuid.UUID) -> Select[tuple[Device]]:
        return (
            select(Device)
            .options(selectinload(Device.assigned_user))
            .where(Device.organization_id == organization_id)
            .execution_options(populate_existing=True)
        )

    async def get_for_organization(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> Device | None:
        result = await self._session.execute(
            self._base_query(organization_id).where(Device.id == device_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_unscoped(self, device_id: uuid.UUID) -> Device | None:
        """The one legitimate bare lookup on this repository — used ONLY by
        the device-credential authentication dependency
        (app/enrollment/dependencies.py::get_enrolled_device), which has no
        organization context to scope by yet (the device is proving its
        own identity via a bearer secret, not an admin session). The
        caller MUST verify `device_secret_hash` before trusting anything
        about the returned row, exactly like
        EnrollmentTokenRepository.get_by_token_hash. Never call this from
        an admin-facing code path."""
        result = await self._session.execute(select(Device).where(Device.id == device_id))
        return result.scalar_one_or_none()

    def _apply_filters(
        self,
        query: Select[_RowT],
        *,
        status: DeviceStatus | None,
        platform: DevicePlatform | None,
        ownership_type: OwnershipType | None,
        enrollment_status: EnrollmentStatus | None,
        assigned_user_id: uuid.UUID | None,
        provider_name: str | None = None,
        search: str | None = None,
    ) -> Select[_RowT]:
        if status is not None:
            query = query.where(Device.status == status)
        if platform is not None:
            query = query.where(Device.platform == platform)
        if ownership_type is not None:
            query = query.where(Device.ownership_type == ownership_type)
        if enrollment_status is not None:
            query = query.where(Device.enrollment_status == enrollment_status)
        if assigned_user_id is not None:
            query = query.where(Device.assigned_user_id == assigned_user_id)
        if provider_name is not None:
            # Phase 12: "managed by provider X" — an inner join is correct
            # here (not outer), since filtering FOR a provider must never
            # match a device with no link at all.
            query = query.join(
                ProviderDeviceLink, ProviderDeviceLink.device_id == Device.id
            ).where(ProviderDeviceLink.provider_name == provider_name)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.outerjoin(User, Device.assigned_user_id == User.id).where(
                or_(
                    Device.display_name.ilike(pattern),
                    Device.serial_number.ilike(pattern),
                    Device.manufacturer.ilike(pattern),
                    Device.model.ilike(pattern),
                    Device.device_identifier.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )
        return query

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        status: DeviceStatus | None = None,
        platform: DevicePlatform | None = None,
        ownership_type: OwnershipType | None = None,
        enrollment_status: EnrollmentStatus | None = None,
        assigned_user_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        search: str | None = None,
        sort: str = "created_at",
        sort_desc: bool = True,
        offset: int = 0,
        limit: int = 25,
    ) -> Sequence[Device]:
        query = self._apply_filters(
            self._base_query(organization_id),
            status=status,
            platform=platform,
            ownership_type=ownership_type,
            enrollment_status=enrollment_status,
            assigned_user_id=assigned_user_id,
            provider_name=provider_name,
            search=search,
        )
        sort_column = _SORT_COLUMNS.get(sort, Device.created_at)
        query = query.order_by(sort_column.desc() if sort_desc else sort_column.asc())  # type: ignore[attr-defined]
        query = query.offset(offset).limit(limit)
        result = await self._session.execute(query)
        return result.scalars().all()

    async def count_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        status: DeviceStatus | None = None,
        platform: DevicePlatform | None = None,
        ownership_type: OwnershipType | None = None,
        enrollment_status: EnrollmentStatus | None = None,
        assigned_user_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        search: str | None = None,
    ) -> int:
        query = self._apply_filters(
            select(func.count(func.distinct(Device.id)))
            .select_from(Device)
            .where(Device.organization_id == organization_id),
            status=status,
            platform=platform,
            ownership_type=ownership_type,
            enrollment_status=enrollment_status,
            assigned_user_id=assigned_user_id,
            provider_name=provider_name,
            search=search,
        )
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def exists_device_identifier(
        self, organization_id: uuid.UUID, device_identifier: str
    ) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(Device)
            .where(
                Device.organization_id == organization_id,
                Device.device_identifier == device_identifier,
            )
        )
        return int(result.scalar_one()) > 0

    async def exists_serial_number(
        self, organization_id: uuid.UUID, serial_number: str
    ) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(Device)
            .where(
                Device.organization_id == organization_id,
                Device.serial_number == serial_number,
            )
        )
        return int(result.scalar_one()) > 0

    async def exists_imei_hash(self, organization_id: uuid.UUID, imei_hash: str) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(Device)
            .where(
                Device.organization_id == organization_id,
                Device.imei_hash == imei_hash,
            )
        )
        return int(result.scalar_one()) > 0

    async def create_for_organization(
        self, organization_id: uuid.UUID, **fields: object
    ) -> Device:
        device = Device(organization_id=organization_id, **fields)
        self._session.add(device)
        await self._session.flush()
        await self._session.refresh(device)
        return device

    async def update_for_organization(self, device: Device, **fields: object) -> Device:
        for key, value in fields.items():
            setattr(device, key, value)
        await self._session.flush()
        # Mirrors OrganizationRepository.update(): updated_at is
        # server-computed (onupdate=func.now()) and left expired after
        # flush; without an explicit refresh a later synchronous
        # Pydantic model_validate() would attempt an implicit lazy-load
        # outside of the async greenlet context and raise MissingGreenlet.
        await self._session.refresh(device)
        return device

    async def set_status(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, status: DeviceStatus
    ) -> None:
        await self._session.execute(
            update(Device)
            .where(Device.id == device_id, Device.organization_id == organization_id)
            .values(status=status)
        )

    async def assign_for_organization(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        await self._session.execute(
            update(Device)
            .where(Device.id == device_id, Device.organization_id == organization_id)
            .values(assigned_user_id=user_id)
        )

    async def unassign_for_organization(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> None:
        await self._session.execute(
            update(Device)
            .where(Device.id == device_id, Device.organization_id == organization_id)
            .values(assigned_user_id=None)
        )

    async def delete_for_organization(
        self, organization_id: uuid.UUID, device: Device
    ) -> None:
        await self._session.delete(device)
        await self._session.flush()
