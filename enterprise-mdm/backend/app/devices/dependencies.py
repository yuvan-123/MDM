"""FastAPI dependencies for device endpoints.

`/devices` is a flat, non-nested resource (no `{organization_id}` path
segment), so tenant context is resolved from the `X-Organization-ID`
header via `get_current_organization`. `require_device_permission` is a
name-preserving alias for the shared
`app.organizations.dependencies.require_current_organization_permission`
(Phase 5 factored the permission-check body out of this file and into that
one, since enrollment/device_groups/policies/compliance all need the
identical header-based pattern — see that function's docstring).
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.devices.service import DeviceService
from app.organizations.dependencies import require_current_organization_permission
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository

require_device_permission = require_current_organization_permission


def get_device_service(db: AsyncSession = Depends(get_db_session)) -> DeviceService:
    return DeviceService(
        device_repo=DeviceRepository(db),
        membership_repo=MembershipRepository(db),
        user_repo=UserRepository(db),
        audit_repo=AuditRepository(db),
    )
