"""Application management service (Phase 14): catalog CRUD, assignment,
deployment, inventory, and compliance/remediation integration.

Deployment is dispatched through the exact same DeviceCommand/CommandService
machinery every other remote device action uses (brief §6: "assignment ->
command -> provider -> result") — this module never talks to a
PlatformManagementProvider directly, and never enqueues its own
BackgroundJob. `INSTALL_APPLICATION`/`UNINSTALL_APPLICATION` are just two
more DeviceCommandType values (see app/commands/service.py); a deployment
"job" IS the COMMAND_DISPATCH job CommandService.create_command already
enqueues for every command type.

## Why this does NOT reuse PolicyViolation/RemediationPolicyConfig verbatim

Brief §10/§11 ask for "required app missing -> policy violation -> reuse
Phase 13 remediation." `PolicyViolation` (app/compliance/models.py) is
structurally scoped to one resolved `Policy` (`policy_id` is a NOT NULL
FK) — but application assignment is independent of policy assignment (a
device can have required apps with no policy assigned at all, or vice
versa), so a required-app failure has no `Policy` row to attach a
`PolicyViolation` to without changing that table's own Phase 13 contract,
which the brief forbids ("do not modify prior migrations"). Likewise
`RemediationPolicyConfig.command_type` (app/policies/schemas.py) is a
single, no-payload command per policy — it has no field for "which
package/bundle identifier," which is inherently per-*application*, not
per-*policy*, information.

So Phase 14 reuses the actual reusable parts at their right layer instead:
- **Compliance status** (brief's real ask — "this device is non-compliant
  because a required app is missing") is reused directly:
  `ComplianceService.evaluate_device` (Phase 5) appends a RuleResult from
  `evaluate_required_applications` below into the exact same overall
  status computation and DeviceCompliance/ComplianceResult history every
  other rule already feeds — no separate app-compliance table.
- **Remediation** ("missing required app -> install command") is the
  deployment workflow itself: the first time a required app is found
  missing for a device, `evaluate_required_applications` auto-triggers
  `deploy_to_device` directly. This needs no `RemediationPolicyConfig`
  opt-in because installing an app the admin explicitly assigned as
  REQUIRED is the direct, intended effect of that assignment — not a
  discretionary automated action needing the same non-destructive-only
  safeguard Phase 13 built for optional, policy-wide, potentially
  disruptive remediation commands. A second automatic attempt is never
  made without an explicit admin retry (`POST /applications/{id}/deploy`
  again) once a deployment reaches FAILED — this mirrors Phase 13's own
  "REMEDIATION_FAILED requires a human" line, just without a numeric
  attempt counter to enforce it, since there is only ever one automatic
  attempt to begin with.

See docs/architecture/phase-14.md for the full writeup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import status

from app.applications.models import (
    Application,
    ApplicationAssignment,
    ApplicationDeployment,
    ApplicationDeploymentStatus,
    ApplicationRequirement,
)
from app.audit.models import AuditEventType
from app.commands.models import DeviceCommandStatus, DeviceCommandType
from app.commands.service import CommandService
from app.compliance.rules import RuleResult
from app.core.errors import AppError
from app.devices.enums import DevicePlatform
from app.devices.models import Device
from app.policies.models import PolicyScopeType
from app.repositories.application_assignment_repository import ApplicationAssignmentRepository
from app.repositories.application_deployment_repository import ApplicationDeploymentRepository
from app.repositories.application_repository import ApplicationRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_group_repository import DeviceGroupRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository

_TERMINAL_COMMAND_STATUSES = frozenset(
    {
        DeviceCommandStatus.COMPLETED,
        DeviceCommandStatus.FAILED,
        DeviceCommandStatus.CANCELLED,
        DeviceCommandStatus.EXPIRED,
    }
)

# A device-group/organization-wide bulk deploy is resolved and dispatched
# synchronously within the issuing request (the caller gets back the full
# per-device result list immediately) — unlike Phase 5's policy-assignment
# fanout, no async pagination job exists for this. This cap is the
# documented boundary of that choice; a scope larger than this needs to be
# split into multiple explicit `device_ids` calls. See
# docs/architecture/phase-14.md "Known limitations."
MAX_BULK_DEPLOY_TARGETS = 500


def _not_found() -> AppError:
    return AppError(
        code="APPLICATION_NOT_FOUND",
        message="Application not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


class ApplicationService:
    def __init__(
        self,
        application_repo: ApplicationRepository,
        assignment_repo: ApplicationAssignmentRepository,
        deployment_repo: ApplicationDeploymentRepository,
        device_repo: DeviceRepository,
        device_group_repo: DeviceGroupRepository,
        membership_repo: MembershipRepository,
        command_repo: DeviceCommandRepository,
        command_service: CommandService,
        audit_repo: AuditRepository,
    ) -> None:
        self._application_repo = application_repo
        self._assignment_repo = assignment_repo
        self._deployment_repo = deployment_repo
        self._device_repo = device_repo
        self._device_group_repo = device_group_repo
        self._membership_repo = membership_repo
        self._command_repo = command_repo
        self._command_service = command_service
        self._audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Catalog CRUD
    # ------------------------------------------------------------------

    async def get_device_or_404(self, organization_id: uuid.UUID, device_id: uuid.UUID) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return device

    async def get_application(
        self, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> Application:
        application = await self._application_repo.get_for_organization(
            organization_id, application_id
        )
        if application is None:
            raise _not_found()
        return application

    async def list_applications(
        self,
        organization_id: uuid.UUID,
        *,
        platform: DevicePlatform | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Application], int]:
        items = await self._application_repo.list_for_organization(
            organization_id, platform=platform, offset=offset, limit=limit
        )
        total = await self._application_repo.count_for_organization(
            organization_id, platform=platform
        )
        return items, total

    async def create_application(
        self,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        name: str,
        platform: DevicePlatform,
        package_name: str | None,
        bundle_identifier: str | None,
        version: str | None,
        description: str | None,
        publisher: str | None,
        managed: bool,
    ) -> Application:
        if await self._application_repo.exists_by_identifier(
            organization_id,
            platform=platform,
            package_name=package_name,
            bundle_identifier=bundle_identifier,
        ):
            raise AppError(
                code="APPLICATION_ALREADY_EXISTS",
                message="An application with this identifier already exists for this platform.",
                status_code=status.HTTP_409_CONFLICT,
            )
        application = await self._application_repo.create(
            organization_id,
            name=name,
            platform=platform,
            package_name=package_name,
            bundle_identifier=bundle_identifier,
            version=version,
            description=description,
            publisher=publisher,
            managed=managed,
            created_by=actor_user_id,
            updated_by=actor_user_id,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"application_id": str(application.id), "name": name},
        )
        return application

    async def update_application(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        name: str | None,
        version: str | None,
        description: str | None,
        publisher: str | None,
        managed: bool | None,
    ) -> Application:
        application = await self.get_application(organization_id, application_id)
        fields: dict[str, object] = {"updated_by": actor_user_id}
        if name is not None:
            fields["name"] = name
        if version is not None:
            fields["version"] = version
        if description is not None:
            fields["description"] = description
        if publisher is not None:
            fields["publisher"] = publisher
        if managed is not None:
            fields["managed"] = managed
        application = await self._application_repo.update_for_organization(application, **fields)
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_UPDATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"application_id": str(application.id), "fields": list(fields.keys())},
        )
        return application

    async def delete_application(
        self, organization_id: uuid.UUID, application_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> None:
        """Hard delete — unlike Policy (versioned, archived not deleted),
        Application has no version history to preserve; ApplicationDeployment
        rows cascade (their own DeviceCommand history remains, since
        DeviceCommand doesn't FK to Application at all)."""
        application = await self.get_application(organization_id, application_id)
        assignment_count = await self._assignment_repo.count_for_application(
            organization_id, application_id
        )
        if assignment_count > 0:
            raise AppError(
                code="APPLICATION_HAS_ASSIGNMENTS",
                message="Unassign this application from every scope before deleting it.",
                status_code=status.HTTP_409_CONFLICT,
            )
        await self._application_repo.delete(application)
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_DELETED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"application_id": str(application_id)},
        )

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    async def _validate_scope(
        self, organization_id: uuid.UUID, scope_type: PolicyScopeType, scope_id: uuid.UUID
    ) -> None:
        """Identical validation to PolicyService._validate_scope — same
        four scope types, same "polymorphic scope_id validated in the
        service layer, not a DB FK" design."""
        if scope_type == PolicyScopeType.ORGANIZATION:
            if scope_id != organization_id:
                raise AppError(
                    code="INVALID_APPLICATION_SCOPE",
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

    async def assign_application(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
        requirement: ApplicationRequirement,
    ) -> ApplicationAssignment:
        await self.get_application(organization_id, application_id)
        await self._validate_scope(organization_id, scope_type, scope_id)

        existing = await self._assignment_repo.get_by_scope(
            organization_id, application_id, scope_type, scope_id
        )
        if existing is not None:
            if existing.requirement != requirement:
                existing = await self._assignment_repo.update(existing, requirement=requirement)
            return existing

        assignment = await self._assignment_repo.create(
            organization_id,
            application_id=application_id,
            scope_type=scope_type,
            scope_id=scope_id,
            requirement=requirement,
            created_at=datetime.now(tz=UTC),
            created_by=actor_user_id,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_ASSIGNED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "application_id": str(application_id),
                "scope_type": scope_type.value,
                "scope_id": str(scope_id),
                "requirement": requirement.value,
            },
        )
        return assignment

    async def unassign_application(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        scope_type: PolicyScopeType,
        scope_id: uuid.UUID,
    ) -> None:
        assignment = await self._assignment_repo.get_by_scope(
            organization_id, application_id, scope_type, scope_id
        )
        if assignment is None:
            raise AppError(
                code="APPLICATION_ASSIGNMENT_NOT_FOUND",
                message="This application is not assigned to that scope.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        await self._assignment_repo.delete(assignment)
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_UNASSIGNED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "application_id": str(application_id),
                "scope_type": scope_type.value,
                "scope_id": str(scope_id),
            },
        )

    async def list_assignments(
        self, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> list[ApplicationAssignment]:
        await self.get_application(organization_id, application_id)
        return await self._assignment_repo.list_for_application(organization_id, application_id)

    # ------------------------------------------------------------------
    # Resolution — which applications apply to one device (union across
    # scopes, NOT single-winner precedence like PolicyResolutionService:
    # multiple required apps from different scopes are not mutually
    # exclusive the way policies are).
    # ------------------------------------------------------------------

    async def resolve_assignments_for_device(
        self, organization_id: uuid.UUID, device: Device
    ) -> dict[uuid.UUID, ApplicationRequirement]:
        """Returns {application_id: effective_requirement}. If the same
        application is assigned at multiple scopes with different
        requirements, REQUIRED wins — an org-wide OPTIONAL assignment never
        downgrades a more specific REQUIRED one, or vice versa."""
        assignments: list[ApplicationAssignment] = []
        assignments.extend(
            await self._assignment_repo.list_for_scope(
                organization_id, PolicyScopeType.DEVICE, device.id
            )
        )
        for group_id in await self._device_group_repo.list_group_ids_for_device(device.id):
            assignments.extend(
                await self._assignment_repo.list_for_scope(
                    organization_id, PolicyScopeType.DEVICE_GROUP, group_id
                )
            )
        if device.assigned_user_id is not None:
            assignments.extend(
                await self._assignment_repo.list_for_scope(
                    organization_id, PolicyScopeType.USER, device.assigned_user_id
                )
            )
        assignments.extend(
            await self._assignment_repo.list_for_scope(
                organization_id, PolicyScopeType.ORGANIZATION, organization_id
            )
        )

        resolved: dict[uuid.UUID, ApplicationRequirement] = {}
        for assignment in assignments:
            current = resolved.get(assignment.application_id)
            if current == ApplicationRequirement.REQUIRED:
                continue
            resolved[assignment.application_id] = assignment.requirement
        return resolved

    async def get_device_applications(
        self, organization_id: uuid.UUID, device: Device
    ) -> list[tuple[Application, ApplicationRequirement, ApplicationDeployment | None]]:
        resolved = await self.resolve_assignments_for_device(organization_id, device)
        results: list[tuple[Application, ApplicationRequirement, ApplicationDeployment | None]] = []
        for application_id, requirement in resolved.items():
            application = await self._application_repo.get_for_organization(
                organization_id, application_id
            )
            if application is None:
                continue
            deployment = await self._deployment_repo.get_by_device_and_application(
                organization_id, device.id, application_id
            )
            results.append((application, requirement, deployment))
        return results

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def _deployment_payload(self, application: Application) -> dict[str, str]:
        if application.package_name:
            return {"package_name": application.package_name}
        if application.bundle_identifier:
            return {"bundle_identifier": application.bundle_identifier}
        raise AppError(
            code="APPLICATION_NOT_DEPLOYABLE",
            message="This application has no platform identifier configured.",
            status_code=status.HTTP_409_CONFLICT,
        )

    async def _get_or_create_deployment(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, application: Application
    ) -> ApplicationDeployment:
        existing = await self._deployment_repo.get_by_device_and_application(
            organization_id, device_id, application.id
        )
        if existing is not None:
            return existing
        return await self._deployment_repo.create(
            organization_id,
            device_id=device_id,
            application_id=application.id,
            assignment_id=None,
            requirement=ApplicationRequirement.OPTIONAL,
            status=ApplicationDeploymentStatus.PENDING,
            attempts=0,
        )

    async def deploy_to_device(
        self,
        organization_id: uuid.UUID,
        application: Application,
        device: Device,
        *,
        assignment_id: uuid.UUID | None,
        requirement: ApplicationRequirement,
        actor_user_id: uuid.UUID | None,
    ) -> ApplicationDeployment:
        """One device, one deployment attempt. Idempotent in the same
        sense as CommandService.create_command: a deployment already
        INSTALLING/INSTALLED (an attempt is in flight or already succeeded)
        is returned unchanged rather than re-dispatched. PENDING is NOT
        skipped here — it means "row created, never yet dispatched" (the
        only state `_get_or_create_deployment` ever produces for a brand
        new row) and must fall through to a real first attempt below; only
        a FAILED or REMOVED row (or this brand-new PENDING one) actually
        triggers a fresh command."""
        deployment = await self._get_or_create_deployment(organization_id, device.id, application)
        if deployment.status in (
            ApplicationDeploymentStatus.INSTALLING,
            ApplicationDeploymentStatus.INSTALLED,
        ):
            return deployment

        payload = self._deployment_payload(application)
        next_attempt = deployment.attempts + 1
        try:
            command = await self._command_service.create_command(
                organization_id,
                device.id,
                command_type=DeviceCommandType.INSTALL_APPLICATION,
                payload=payload,
                idempotency_key=f"app_deploy:{application.id}:{device.id}:{next_attempt}",
                actor_user_id=actor_user_id,
            )
        except AppError as exc:
            deployment = await self._deployment_repo.update(
                deployment,
                status=ApplicationDeploymentStatus.FAILED,
                attempts=next_attempt,
                last_error=exc.message,
            )
            return deployment

        deployment = await self._deployment_repo.update(
            deployment,
            assignment_id=assignment_id,
            requirement=requirement,
            status=ApplicationDeploymentStatus.INSTALLING,
            attempts=next_attempt,
            last_command_id=command.id,
            last_error=None,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_DEPLOYMENT_TRIGGERED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "application_id": str(application.id),
                "device_id": str(device.id),
                "deployment_id": str(deployment.id),
                "command_id": str(command.id),
            },
        )
        return deployment

    async def remove_from_device(
        self,
        organization_id: uuid.UUID,
        application: Application,
        device: Device,
        *,
        actor_user_id: uuid.UUID,
    ) -> ApplicationDeployment:
        """Managed removal — only legal once a deployment exists and is
        INSTALLED (brief §14: "support managed app removal where provider
        supports it"; an app never dispatched to this device has nothing
        to remove)."""
        deployment = await self._deployment_repo.get_by_device_and_application(
            organization_id, device.id, application.id
        )
        if deployment is None or deployment.status != ApplicationDeploymentStatus.INSTALLED:
            raise AppError(
                code="APPLICATION_NOT_INSTALLED",
                message="This application is not currently installed on this device.",
                status_code=status.HTTP_409_CONFLICT,
            )
        payload = self._deployment_payload(application)
        next_attempt = deployment.attempts + 1
        command = await self._command_service.create_command(
            organization_id,
            device.id,
            command_type=DeviceCommandType.UNINSTALL_APPLICATION,
            payload=payload,
            idempotency_key=f"app_remove:{application.id}:{device.id}:{next_attempt}",
            actor_user_id=actor_user_id,
        )
        deployment = await self._deployment_repo.update(
            deployment,
            status=ApplicationDeploymentStatus.INSTALLING,
            attempts=next_attempt,
            last_command_id=command.id,
            last_error=None,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.APPLICATION_REMOVAL_TRIGGERED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "application_id": str(application.id),
                "device_id": str(device.id),
                "deployment_id": str(deployment.id),
                "command_id": str(command.id),
            },
        )
        return deployment

    async def _resolve_bulk_targets(
        self,
        organization_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None,
        device_ids: list[uuid.UUID] | None,
        device_group_id: uuid.UUID | None,
        organization_wide: bool,
    ) -> list[Device]:
        if device_id is not None:
            device = await self._device_repo.get_for_organization(organization_id, device_id)
            if device is None:
                raise AppError(
                    code="DEVICE_NOT_FOUND",
                    message="Device not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            return [device]

        if device_ids is not None:
            devices: list[Device] = []
            for did in device_ids:
                device = await self._device_repo.get_for_organization(organization_id, did)
                if device is None:
                    raise AppError(
                        code="DEVICE_NOT_FOUND",
                        message=f"Device {did} not found.",
                        status_code=status.HTTP_404_NOT_FOUND,
                    )
                devices.append(device)
            return devices

        if device_group_id is not None:
            group = await self._device_group_repo.get_for_organization(
                organization_id, device_group_id
            )
            if group is None:
                raise AppError(
                    code="DEVICE_GROUP_NOT_FOUND",
                    message="Device group not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            count = await self._device_group_repo.count_devices_for_group(device_group_id)
            if count > MAX_BULK_DEPLOY_TARGETS:
                raise AppError(
                    code="BULK_DEPLOY_TOO_LARGE",
                    message=f"Device group has {count} devices — bulk deploy is capped at "
                    f"{MAX_BULK_DEPLOY_TARGETS}. Deploy to explicit device_ids in batches instead.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            return await self._device_group_repo.list_devices_for_group(
                device_group_id, limit=MAX_BULK_DEPLOY_TARGETS
            )

        # organization_wide
        count = await self._device_repo.count_for_organization(organization_id)
        if count > MAX_BULK_DEPLOY_TARGETS:
            raise AppError(
                code="BULK_DEPLOY_TOO_LARGE",
                message=f"Organization has {count} devices — bulk deploy is capped at "
                f"{MAX_BULK_DEPLOY_TARGETS}. Deploy to explicit device_ids in batches instead.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return list(
            await self._device_repo.list_for_organization(
                organization_id, limit=MAX_BULK_DEPLOY_TARGETS
            )
        )

    async def deploy(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        device_id: uuid.UUID | None,
        device_ids: list[uuid.UUID] | None,
        device_group_id: uuid.UUID | None,
        organization_wide: bool,
    ) -> list[ApplicationDeployment]:
        """Brief §13: each target device gets its own ApplicationDeployment
        row, its own DeviceCommand, and its own status/history — this loop
        is that guarantee; nothing here dispatches one shared command for
        multiple devices."""
        application = await self.get_application(organization_id, application_id)
        devices = await self._resolve_bulk_targets(
            organization_id,
            device_id=device_id,
            device_ids=device_ids,
            device_group_id=device_group_id,
            organization_wide=organization_wide,
        )
        return [
            await self.deploy_to_device(
                organization_id,
                application,
                device,
                assignment_id=None,
                requirement=ApplicationRequirement.OPTIONAL,
                actor_user_id=actor_user_id,
            )
            for device in devices
        ]

    async def list_deployments(
        self,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        deployment_status: ApplicationDeploymentStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ApplicationDeployment], int]:
        await self.get_application(organization_id, application_id)
        items = await self._deployment_repo.list_for_application(
            organization_id,
            application_id,
            device_id=device_id,
            status=deployment_status,
            offset=offset,
            limit=limit,
        )
        total = await self._deployment_repo.count_for_application(
            organization_id, application_id, device_id=device_id, status=deployment_status
        )
        return items, total

    # ------------------------------------------------------------------
    # Reconciliation + compliance integration (called from
    # ComplianceService.evaluate_device — same per-heartbeat choke point
    # Phase 13's violation reconciliation already runs from).
    # ------------------------------------------------------------------

    async def reconcile_device_deployments(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> None:
        """Advances any in-flight deployment whose underlying DeviceCommand
        has since reached a terminal state — mirrors
        ComplianceService._reconcile_violations' own command-status check
        for REMEDIATING violations."""
        for deployment in await self._deployment_repo.list_in_flight_for_device(
            organization_id, device_id
        ):
            if deployment.last_command_id is None:
                continue
            command = await self._command_repo.get_for_organization(
                organization_id, deployment.last_command_id
            )
            if command is None or command.status not in _TERMINAL_COMMAND_STATUSES:
                continue
            now = datetime.now(tz=UTC)
            if command.status == DeviceCommandStatus.COMPLETED:
                if command.command_type == DeviceCommandType.UNINSTALL_APPLICATION:
                    await self._deployment_repo.update(
                        deployment,
                        status=ApplicationDeploymentStatus.REMOVED,
                        removed_at=now,
                        last_error=None,
                    )
                else:
                    await self._deployment_repo.update(
                        deployment,
                        status=ApplicationDeploymentStatus.INSTALLED,
                        installed_at=now,
                        last_error=None,
                    )
            else:
                await self._deployment_repo.update(
                    deployment,
                    status=ApplicationDeploymentStatus.FAILED,
                    last_error=command.error_message,
                )

    async def evaluate_required_applications(
        self, organization_id: uuid.UUID, device: Device, *, actor_user_id: uuid.UUID | None
    ) -> RuleResult | None:
        """Called from ComplianceService.evaluate_device, appended to the
        same `results` list every RULES function feeds — see this module's
        own docstring for why this is a direct RuleResult rather than a
        PolicyViolation. Returns None (rule doesn't apply, same convention
        as every function in app/compliance/rules.py) when this device has
        no REQUIRED application assignments at all."""
        resolved = await self.resolve_assignments_for_device(organization_id, device)
        required_ids = [
            app_id
            for app_id, requirement in resolved.items()
            if requirement == ApplicationRequirement.REQUIRED
        ]
        if not required_ids:
            return None

        missing_names: list[str] = []
        for application_id in required_ids:
            application = await self._application_repo.get_for_organization(
                organization_id, application_id
            )
            if application is None:
                continue
            deployment = await self._deployment_repo.get_by_device_and_application(
                organization_id, device.id, application_id
            )
            if (
                deployment is not None
                and deployment.status == ApplicationDeploymentStatus.INSTALLED
            ):
                continue
            missing_names.append(application.name)
            # Auto-remediate: the first automatic deployment attempt for a
            # required app is triggered directly from here — see this
            # module's docstring for why this doesn't route through
            # RemediationPolicyConfig. deploy_to_device is itself
            # idempotent (no-op if already PENDING/INSTALLING/INSTALLED),
            # so this is safe to call on every evaluation without risking a
            # redundant dispatch; a FAILED deployment is NOT automatically
            # retried here — that requires an explicit admin action,
            # mirroring Phase 13's REMEDIATION_FAILED needing a human.
            if deployment is None:
                await self.deploy_to_device(
                    organization_id,
                    application,
                    device,
                    assignment_id=None,
                    requirement=ApplicationRequirement.REQUIRED,
                    actor_user_id=actor_user_id,
                )

        if missing_names:
            return RuleResult(
                "REQUIRED_APPLICATIONS_INSTALLED",
                "FAIL",
                f"Missing required application(s): {', '.join(sorted(missing_names))}.",
                expected="installed",
                actual="missing",
            )
        return RuleResult(
            "REQUIRED_APPLICATIONS_INSTALLED",
            "PASS",
            "All required applications are installed.",
            expected="installed",
            actual="installed",
        )
