"""FastAPI dependencies for the policies module. Same header-based
tenant-context pattern as app/devices/dependencies.py."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db_session
from app.jobs.service import JobService
from app.organizations.dependencies import require_current_organization_permission
from app.policies.service import PolicyResolutionService, PolicyService
from app.repositories.audit_repository import AuditRepository
from app.repositories.background_job_repository import BackgroundJobRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.policy_assignment_repository import PolicyAssignmentRepository
from app.repositories.policy_repository import PolicyRepository

require_policy_permission = require_current_organization_permission


def get_policy_service(db: AsyncSession = Depends(get_db_session)) -> PolicyService:
    return PolicyService(
        policy_repo=PolicyRepository(db),
        assignment_repo=PolicyAssignmentRepository(db),
        device_repo=DeviceRepository(db),
        device_group_repo=DeviceGroupRepository(db),
        membership_repo=MembershipRepository(db),
        audit_repo=AuditRepository(db),
        job_service=JobService(BackgroundJobRepository(db), AuditRepository(db)),
    )


def get_policy_resolution_service(
    db: AsyncSession = Depends(get_db_session),
) -> PolicyResolutionService:
    return PolicyResolutionService(
        policy_repo=PolicyRepository(db),
        assignment_repo=PolicyAssignmentRepository(db),
        device_group_repo=DeviceGroupRepository(db),
    )
