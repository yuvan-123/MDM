"""FastAPI dependencies for the device command module.

Admin-authenticated only (cookie + CSRF, like every other Phase 4/5
mutating endpoint) — see app/commands/router.py's module docstring for why
no device-credential channel is needed here.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.commands.service import CommandService
from app.database.session import get_db_session
from app.jobs.service import JobService
from app.platform.registry import PlatformProviderRegistry
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository


def get_command_service(db: AsyncSession = Depends(get_db_session)) -> CommandService:
    return CommandService(
        command_repo=DeviceCommandRepository(db),
        device_repo=DeviceRepository(db),
        provider_registry=PlatformProviderRegistry(),
        audit_repo=AuditRepository(db),
        job_service=JobService(BackgroundJobRepository(db), AuditRepository(db)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db),
    )
