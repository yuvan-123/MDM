"""FastAPI dependencies for the device groups module. Same header-based
tenant-context pattern as app/devices/dependencies.py — see that module's
docstring and app.organizations.dependencies.require_current_organization_permission."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.device_groups.service import DeviceGroupService
from app.organizations.dependencies import require_current_organization_permission
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository

require_device_group_permission = require_current_organization_permission


def get_device_group_service(db: AsyncSession = Depends(get_db_session)) -> DeviceGroupService:
    return DeviceGroupService(
        group_repo=DeviceGroupRepository(db),
        device_repo=DeviceRepository(db),
        audit_repo=AuditRepository(db),
    )
