"""FastAPI dependencies for the applications module. Same header-based
tenant-context pattern as app/policies/dependencies.py."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.service import ApplicationService
from app.commands.service import CommandService
from app.database.session import get_db_session
from app.jobs.service import JobService
from app.organizations.dependencies import require_current_organization_permission
from app.platform.registry import PlatformProviderRegistry
from app.repositories.application_assignment_repository import ApplicationAssignmentRepository
from app.repositories.application_deployment_repository import ApplicationDeploymentRepository
from app.repositories.application_repository import ApplicationRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

require_application_permission = require_current_organization_permission


def get_application_service(db: AsyncSession = Depends(get_db_session)) -> ApplicationService:
    command_service = CommandService(
        command_repo=DeviceCommandRepository(db),
        device_repo=DeviceRepository(db),
        provider_registry=PlatformProviderRegistry(),
        audit_repo=AuditRepository(db),
        job_service=JobService(BackgroundJobRepository(db), AuditRepository(db)),
        provider_device_link_repo=ProviderDeviceLinkRepository(db),
    )
    return ApplicationService(
        application_repo=ApplicationRepository(db),
        assignment_repo=ApplicationAssignmentRepository(db),
        deployment_repo=ApplicationDeploymentRepository(db),
        device_repo=DeviceRepository(db),
        device_group_repo=DeviceGroupRepository(db),
        membership_repo=MembershipRepository(db),
        command_repo=DeviceCommandRepository(db),
        command_service=command_service,
        audit_repo=AuditRepository(db),
    )
