"""Policy service — CRUD, versioning, assignment, and precedence resolution.

Constructs no raw SQL/ORM queries — delegates strictly to repositories.

Versioning (STEP 8): create_policy always creates version 1 alongside the
Policy row; update_policy, when given a new `configuration`, always
creates a brand-new PolicyVersion and repoints `current_version_id` —
existing PolicyVersion rows are never mutated in place.

Precedence (STEP 7): PolicyResolutionService.resolve_for_device implements
    DEVICE > DEVICE_GROUP > USER > ORGANIZATION
looking at exactly one candidate assignment per scope level and stopping
at the first ACTIVE-status policy found, in that order. Only ACTIVE
policies are ever considered "in effect" — a DRAFT policy can be assigned
ahead of time but does not affect compliance evaluation until published.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.devices.models import Device
from app.jobs.service import JobService
from app.policies.models import (
    Policy,
    PolicyAssignment,
    PolicyScopeType,
    PolicyStatus,
    PolicyVersion,
)
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.policy_assignment_repository import PolicyAssignmentRepository
from app.repositories.policy_repository import PolicyRepository

# Status transitions reachable through UpdatePolicyRequest.status.
# ARCHIVED is reachable only via delete_policy().
_STATUS_TRANSITIONS: dict[PolicyStatus, frozenset[PolicyStatus]] = {
    PolicyStatus.DRAFT: frozenset({PolicyStatus.ACTIVE}),
    PolicyStatus.ACTIVE: frozenset({PolicyStatus.DRAFT}),
    PolicyStatus.ARCHIVED: frozenset(),
}


def _not_found() -> AppError:
    return AppError(
        code="POLICY_NOT_FOUND", message="Policy not found.", status_code=status.HTTP_404_NOT_FOUND
    )


class PolicyService:
    def __init__(
        self,
        policy_repo: PolicyRepository,
        assignment_repo: PolicyAssignmentRepository,
        device_repo: DeviceRepository,
        device_group_repo: DeviceGroupRepository,
        membership_repo: MembershipRepository,
        audit_repo: AuditRepository,
        job_service: JobService,
    ) -> None:
        self._policy_repo = policy_repo
        self._assignment_repo = assignment_repo
        self._device_repo = device_repo
        self._device_group_repo = device_group_repo
        self._membership_repo = membership_repo
        self._audit_repo = audit_repo
        self._job_service = job_service

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def get_policy(self, organization_id: uuid.UUID, policy_id: uuid.UUID) -> Policy:
        policy = await self._policy_repo.get_for_organization(organization_id, policy_id)
        if policy is None:
            raise _not_found()
        return policy

    async def list_policies(
        self,
        organization_id: uuid.UUID,
        *,
        policy_status: PolicyStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Policy], int]:
        policies = await self._policy_repo.list_for_organization(
            organization_id, status=policy_status, offset=offset, limit=limit
        )
        total = await self._policy_repo.count_for_organization(
            organization_id, status=policy_status
        )
        return policies, total

    async def create_policy(
        self,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        name: str,
        description: str | None,
        configuration: dict[str, object],
    ) -> Policy:
        if await self._policy_repo.exists_by_name(organization_id, name):
            raise AppError(
                code="POLICY_ALREADY_EXISTS",
                message="A policy with this name already exists in this organization.",
                status_code=status.HTTP_409_CONFLICT,
            )
        policy = await self._policy_repo.create(
            organization_id,
            name=name,
            description=description,
            status=PolicyStatus.DRAFT,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        version = await self._policy_repo.create_version(
            policy_id=policy.id,
            organization_id=organization_id,
            version=1,
            configuration=configuration,
            created_at=datetime.now(tz=UTC),
            created_by=actor_user_id,
        )
        policy = await self._policy_repo.update_for_organization(
            policy, current_version_id=version.id
        )
        # update_for_organization only refreshes scalar columns; the
        # lazy="noload" relationship isn't re-fetched automatically, so
        # set it directly from the object we already hold rather than
        # issuing an extra query.
        policy.current_version = version

        await self._audit_repo.log_event(
            event_type=AuditEventType.POLICY_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"policy_id": str(policy.id), "name": name},
        )
        return policy

    async def update_policy(
        self,
        organization_id: uuid.UUID,
        policy_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        name: str | None,
        description: str | None,
        new_status: PolicyStatus | None,
        configuration: dict[str, object] | None,
    ) -> Policy:
        policy = await self.get_policy(organization_id, policy_id)

        if new_status is not None and new_status != policy.status:
            if new_status not in _STATUS_TRANSITIONS.get(policy.status, frozenset()):
                raise AppError(
                    code="INVALID_POLICY_STATUS_TRANSITION",
                    message=(
                        f"Cannot transition policy from {policy.status.value} "
                        f"to {new_status.value}."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                )

        if name is not None and name != policy.name:
            if await self._policy_repo.exists_by_name(organization_id, name):
                raise AppError(
                    code="POLICY_ALREADY_EXISTS",
                    message="A policy with this name already exists in this organization.",
                    status_code=status.HTTP_409_CONFLICT,
                )

        fields: dict[str, object] = {"updated_by": actor_user_id}
        if name is not None:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if new_status is not None:
            fields["status"] = new_status

        changed_config = False
        if configuration is not None:
            latest = await self._policy_repo.get_latest_version_number(policy.id)
            version = await self._policy_repo.create_version(
                policy_id=policy.id,
                organization_id=organization_id,
                version=latest + 1,
                configuration=configuration,
                created_at=datetime.now(tz=UTC),
                created_by=actor_user_id,
            )
            fields["current_version_id"] = version.id
            changed_config = True

        policy = await self._policy_repo.update_for_organization(policy, **fields)
        if changed_config:
            policy.current_version = version

        await self._audit_repo.log_event(
            event_type=AuditEventType.POLICY_UPDATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "policy_id": str(policy.id),
                "fields": list(fields.keys()),
                "new_version": changed_config,
            },
        )
        return policy

    async def delete_policy(
        self, organization_id: uuid.UUID, policy_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> Policy:
        """Archives the policy (status -> ARCHIVED). Not a hard delete —
        PolicyVersion/ComplianceResult history references it and must
        remain intact (STEP 8: "do not silently destroy historical
        configuration"). Blocked while any assignment still references it."""
        policy = await self.get_policy(organization_id, policy_id)
        assignment_count = await self._assignment_repo.count_for_policy(organization_id, policy_id)
        if assignment_count > 0:
            raise AppError(
                code="POLICY_HAS_ASSIGNMENTS",
                message="Unassign this policy from every scope before deleting it.",
                status_code=status.HTTP_409_CONFLICT,
            )
        policy = await self._policy_repo.update_for_organization(
            policy, status=PolicyStatus.ARCHIVED, updated_by=actor_user_id
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.POLICY_DELETED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"policy_id": str(policy.id)},
        )
        return policy

    async def list_versions(self, policy_id: uuid.UUID) -> list[PolicyVersion]:
        return await self._policy_repo.list_versions_for_policy(policy_id)

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    async def _validate_scope(
        self, organization_id: uuid.UUID, scope_type: PolicyScopeType, scope_id: uuid.UUID
    ) -> None:
        if scope_type == PolicyScopeType.ORGANIZATION:
            if scope_id != organization_id:
                raise AppError(
                    code="INVALID_POLICY_SCOPE",
                    message="ORGANIZATION scope_id must be the caller's own organization.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        elif scope_type == PolicyScopeType.USER:
            membership = await self._membership_repo.get_by_user_and_org(scope_id, organization_id)
            if membership is None:
                raise AppError(
                    code="USER_NOT_IN_ORGANIZATION",
                    message="Target user is not a member of this organization.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
        elif scope_type == PolicyScopeType.DEVICE_GROUP:
            group = await self._device_group_repo.get_for_organization(organization_id, scope_id)
            if group is None:
                raise AppError(
                    code="DEVICE_GROUP_NOT_FOUND",
                    message="Device group not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
        elif scope_type == PolicyScopeType.DEVICE:
            device = await self._device_repo.get_for_organization(organization_id, scope_id)
            if device is None:
                raise AppError(
                    code="DEVICE_NOT_FOUND",
                    message="Device not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

    async def assign_policy(
        self,
        organization_id: uuid.UUID,
        policy_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> PolicyAssignment:
        await self.get_policy(organization_id, policy_id)  # 404 if missing/foreign
        await self._validate_scope(organization_id, scope_type, scope_id)

        existing = await self._assignment_repo.get_by_scope(
            organization_id, policy_id, scope_type, scope_id
        )
        if existing is not None:
            return existing

        assignment = await self._assignment_repo.create(
            organization_id,
            policy_id=policy_id,
            scope_type=scope_type,
            scope_id=scope_id,
            created_at=datetime.now(tz=UTC),
            created_by=actor_user_id,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.POLICY_ASSIGNED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "policy_id": str(policy_id),
                "scope_type": scope_type.value,
                "scope_id": str(scope_id),
            },
        )
        # Policy-change-triggered compliance re-evaluation runs off the
        # worker queue, not inline — a DEVICE_GROUP/ORGANIZATION-scoped
        # assignment can cover far too many devices to evaluate within this
        # request. See app/jobs/service.py::enqueue_policy_reevaluation and
        # app/workers/loop.py's paginated fanout.
        await self._job_service.enqueue_policy_reevaluation(
            organization_id, scope_type=scope_type, scope_id=scope_id
        )
        return assignment

    async def unassign_policy(
        self,
        organization_id: uuid.UUID,
        policy_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> None:
        assignment = await self._assignment_repo.get_by_scope(
            organization_id, policy_id, scope_type, scope_id
        )
        if assignment is None:
            raise AppError(
                code="POLICY_ASSIGNMENT_NOT_FOUND",
                message="This policy is not assigned to that scope.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        await self._assignment_repo.delete(assignment)
        await self._audit_repo.log_event(
            event_type=AuditEventType.POLICY_UNASSIGNED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "policy_id": str(policy_id),
                "scope_type": scope_type.value,
                "scope_id": str(scope_id),
            },
        )
        # Devices previously covered by the removed assignment must be
        # re-evaluated too — they may now fall back to a lower-precedence
        # policy or to UNKNOWN. Same fanout path as assign_policy.
        await self._job_service.enqueue_policy_reevaluation(
            organization_id, scope_type=scope_type, scope_id=scope_id
        )

    async def list_assignments(
        self, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> list[PolicyAssignment]:
        await self.get_policy(organization_id, policy_id)
        return await self._assignment_repo.list_for_policy(organization_id, policy_id)


class PolicyResolutionService:
    """Precedence resolution: DEVICE > DEVICE_GROUP > USER > ORGANIZATION.
    Used by app/compliance/service.py — kept separate from PolicyService so
    the compliance engine depends on a narrow, read-only surface."""

    def __init__(
        self,
        policy_repo: PolicyRepository,
        assignment_repo: PolicyAssignmentRepository,
        device_group_repo: DeviceGroupRepository,
    ) -> None:
        self._policy_repo = policy_repo
        self._assignment_repo = assignment_repo
        self._device_group_repo = device_group_repo

    async def _active_policy_for_assignments(
        self, organization_id: uuid.UUID, assignments: list[PolicyAssignment]
    ) -> tuple[Policy, PolicyVersion] | None:
        # Most-recently-assigned first — the tie-break when multiple
        # candidates exist at the same precedence level (e.g. a device in
        # two groups that both have a policy assigned).
        for assignment in sorted(assignments, key=lambda a: a.created_at, reverse=True):
            policy = await self._policy_repo.get_for_organization(
                organization_id, assignment.policy_id
            )
            if policy is None or policy.status != PolicyStatus.ACTIVE:
                continue
            if policy.current_version_id is None:
                continue
            version = await self._policy_repo.get_version_for_organization(
                organization_id, policy.current_version_id
            )
            if version is None:
                continue
            return policy, version
        return None

    async def resolve_for_device(
        self, organization_id: uuid.UUID, device: Device
    ) -> tuple[Policy, PolicyVersion] | None:
        device_assignments = await self._assignment_repo.list_for_scope(
            organization_id, PolicyScopeType.DEVICE, device.id
        )
        resolved = await self._active_policy_for_assignments(organization_id, device_assignments)
        if resolved:
            return resolved

        group_ids = await self._device_group_repo.list_group_ids_for_device(device.id)
        group_assignments: list[PolicyAssignment] = []
        for group_id in group_ids:
            group_assignments.extend(
                await self._assignment_repo.list_for_scope(
                    organization_id, PolicyScopeType.DEVICE_GROUP, group_id
                )
            )
        resolved = await self._active_policy_for_assignments(organization_id, group_assignments)
        if resolved:
            return resolved

        if device.assigned_user_id is not None:
            user_assignments = await self._assignment_repo.list_for_scope(
                organization_id, PolicyScopeType.USER, device.assigned_user_id
            )
            resolved = await self._active_policy_for_assignments(organization_id, user_assignments)
            if resolved:
                return resolved

        org_assignments = await self._assignment_repo.list_for_scope(
            organization_id, PolicyScopeType.ORGANIZATION, organization_id
        )
        return await self._active_policy_for_assignments(organization_id, org_assignments)
