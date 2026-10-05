"""FastAPI dependencies for the compliance module. Same header-based
tenant-context pattern as app/devices/dependencies.py."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.applications.service import ApplicationService
from app.commands.service import CommandService
from app.compliance.service import ComplianceService
from app.database.session import get_db_session
from app.jobs.service import JobService
from app.organizations.dependencies import require_current_organization_permission
from app.platform.registry import PlatformProviderRegistry
from app.policies.service import PolicyResolutionService
from app.repositories.application_assignment_repository import ApplicationAssignmentRepository
from app.repositories.application_deployment_repository import ApplicationDeploymentRepository
from app.repositories.application_repository import ApplicationRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.compliance_repository import ComplianceRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.policy_assignment_repository import PolicyAssignmentRepository
from app.repositories.policy_repository import PolicyRepository
from app.repositories.policy_violation_repository import PolicyViolationRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository

require_compliance_permission = require_current_organization_permission


def _build_application_service(db: AsyncSession, audit_repo: AuditRepository) -> ApplicationService:
    command_service = CommandService(
        command_repo=DeviceCommandRepository(db),
        device_repo=DeviceRepository(db),
        provider_registry=PlatformProviderRegistry(),
        audit_repo=audit_repo,
        job_service=JobService(BackgroundJobRepository(db), audit_repo),
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
        audit_repo=audit_repo,
    )


def get_compliance_service(db: AsyncSession = Depends(get_db_session)) -> ComplianceService:
    resolution_service = PolicyResolutionService(
        policy_repo=PolicyRepository(db),
        assignment_repo=PolicyAssignmentRepository(db),
        device_group_repo=DeviceGroupRepository(db),
    )
    audit_repo = AuditRepository(db)
    return ComplianceService(
        compliance_repo=ComplianceRepository(db),
        device_repo=DeviceRepository(db),
        resolution_service=resolution_service,
        audit_repo=audit_repo,
        violation_repo=PolicyViolationRepository(db),
        command_repo=DeviceCommandRepository(db),
        job_service=JobService(BackgroundJobRepository(db), audit_repo),
        policy_repo=PolicyRepository(db),
        application_service=_build_application_service(db, audit_repo),
    )
