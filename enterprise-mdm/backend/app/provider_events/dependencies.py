"""FastAPI dependencies for the provider-events module.

`require_provider_event_permission` uses the same header-based
(X-Organization-ID) tenant-context pattern as jobs/devices/policies —
provider events are a flat, non-nested resource (see
app/provider_events/router.py). `get_provider_event_service` backs both
the admin (read/retry) router and the Phase 9 webhook routers
(app/platform/android/router.py, app/platform/apple/router.py).
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.organizations.dependencies import require_current_organization_permission
from app.provider_events.link_service import ProviderDeviceLinkService
from app.provider_events.service import ProviderEventService
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository
from app.repositories.provider_event_repository import ProviderEventRepository

require_provider_event_permission = require_current_organization_permission
require_provider_device_link_permission = require_current_organization_permission


def get_provider_event_service(db: AsyncSession = Depends(get_db_session)) -> ProviderEventService:
    return ProviderEventService(
        ProviderEventRepository(db),
        DeviceRepository(db),
        ProviderDeviceLinkRepository(db),
        db,
        AuditRepository(db),
    )


def get_provider_device_link_service(
    db: AsyncSession = Depends(get_db_session),
) -> ProviderDeviceLinkService:
    return ProviderDeviceLinkService(
        ProviderDeviceLinkRepository(db), DeviceRepository(db), AuditRepository(db)
    )
