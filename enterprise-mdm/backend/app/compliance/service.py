"""Compliance service — runs the rule registry (app/compliance/rules.py)
against a device's resolved policy (app/policies/service.py's
PolicyResolutionService) and records the result.

Constructs no raw SQL/ORM queries — delegates strictly to
ComplianceRepository, DeviceRepository, and PolicyResolutionService.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import status

from app.applications.service import ApplicationService
from app.audit.models import AuditEventType
from app.commands.models import DeviceCommandStatus
from app.compliance.models import (
    ComplianceStatus,
    DeviceCompliance,
    PolicyViolation,
    ViolationStatus,
)
from app.compliance.rules import RULES, RuleResult
from app.core.errors import AppError
from app.devices.models import Device
from app.jobs.service import JobService
from app.policies.models import Policy, PolicyStatus, PolicyVersion
from app.policies.schemas import PolicyConfiguration, RemediationPolicyConfig
from app.policies.service import PolicyResolutionService
from app.repositories.audit_repository import AuditRepository
from app.repositories.compliance_repository import ComplianceRepository
from app.repositories.device_command_repository import DeviceCommandRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.policy_repository import PolicyRepository
from app.repositories.policy_violation_repository import PolicyViolationRepository

# Terminal DeviceCommand statuses — mirrors app/commands/service.py's own
# _TERMINAL_STATUSES (not imported directly to avoid a compliance->commands
# module-level coupling beyond the one read-only repository call already
# needed; this set is small and stable, unlikely to drift unnoticed).
_TERMINAL_COMMAND_STATUSES = frozenset(
    {
        DeviceCommandStatus.COMPLETED,
        DeviceCommandStatus.FAILED,
        DeviceCommandStatus.CANCELLED,
        DeviceCommandStatus.EXPIRED,
    }
)


def _overall_status(results: list[RuleResult]) -> ComplianceStatus:
    if any(r.outcome == "FAIL" for r in results):
        return ComplianceStatus.NON_COMPLIANT
    if any(r.outcome == "UNKNOWN" for r in results):
        return ComplianceStatus.UNKNOWN
    return ComplianceStatus.COMPLIANT


class ComplianceService:
    def __init__(
        self,
        compliance_repo: ComplianceRepository,
        device_repo: DeviceRepository,
        resolution_service: PolicyResolutionService,
        audit_repo: AuditRepository,
        violation_repo: PolicyViolationRepository,
        command_repo: DeviceCommandRepository,
        job_service: JobService,
        policy_repo: PolicyRepository,
        application_service: ApplicationService,
    ) -> None:
        self._compliance_repo = compliance_repo
        self._device_repo = device_repo
        self._resolution_service = resolution_service
        self._audit_repo = audit_repo
        self._violation_repo = violation_repo
        self._command_repo = command_repo
        self._job_service = job_service
        self._policy_repo = policy_repo
        self._application_service = application_service

    async def evaluate_device(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None,
    ) -> DeviceCompliance:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        resolved = await self._resolution_service.resolve_for_device(organization_id, device)

        if resolved is None:
            results: list[RuleResult] = [
                RuleResult(
                    "NO_POLICY_ASSIGNED",
                    "UNKNOWN",
                    "No active policy applies to this device.",
                )
            ]
            policy_id = None
            policy_version_id = None
        else:
            policy, version = resolved
            config = PolicyConfiguration.model_validate(version.configuration)
            results = [r for rule in RULES if (r := rule(device, config)) is not None]
            policy_id = policy.id
            policy_version_id = version.id

        # Phase 14: reconcile any in-flight app deployment against its
        # underlying DeviceCommand, then fold "required application(s)
        # missing" into this same overall-status computation — see
        # app/applications/service.py's own module docstring for why this
        # is a direct RuleResult rather than a PolicyViolation (application
        # assignment is independent of policy assignment, so it has no
        # Policy row to attach one to).
        await self._application_service.reconcile_device_deployments(organization_id, device.id)
        app_result = await self._application_service.evaluate_required_applications(
            organization_id, device, actor_user_id=actor_user_id
        )
        if app_result is not None:
            results.append(app_result)

        overall = _overall_status(results)
        now = datetime.now(tz=UTC)
        details = [
            {
                "code": r.code,
                "outcome": r.outcome,
                "message": r.message,
                "expected": r.expected,
                "actual": r.actual,
            }
            for r in results
        ]

        compliance = await self._compliance_repo.upsert(
            organization_id,
            device_id,
            status=overall,
            policy_id=policy_id,
            policy_version_id=policy_version_id,
            details=details,
            evaluated_at=now,
        )
        await self._compliance_repo.create_result(
            organization_id,
            device_id,
            policy_id=policy_id,
            policy_version_id=policy_version_id,
            status=overall,
            details=details,
            evaluated_at=now,
            evaluated_by=actor_user_id,
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.COMPLIANCE_EVALUATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device_id), "status": overall.value},
        )

        # Phase 13: violation lifecycle + automated remediation decision.
        # Runs on every evaluation (heartbeat, manual, fanout) — the same
        # single choke point every trigger already goes through, so no
        # second reevaluation path is introduced.
        await self._reconcile_violations(organization_id, device, resolved, results)

        return compliance

    # ------------------------------------------------------------------
    # Violation lifecycle + remediation (Phase 13)
    # ------------------------------------------------------------------

    async def _reconcile_violations(
        self,
        organization_id: uuid.UUID,
        device: Device,
        resolved: tuple[Policy, PolicyVersion] | None,
        results: list[RuleResult],
    ) -> None:
        resolved_policy_id = resolved[0].id if resolved else None
        resolved_version_id = resolved[1].id if resolved else None
        remediation_config: RemediationPolicyConfig | None = None
        if resolved is not None:
            config = PolicyConfiguration.model_validate(resolved[1].configuration)
            remediation_config = config.remediation

        failed: dict[str, RuleResult] = {r.code: r for r in results if r.outcome == "FAIL"}
        now = datetime.now(tz=UTC)

        newly_open: list[PolicyViolation] = []
        handled_codes: set[str] = set()

        for violation in await self._violation_repo.list_active_for_device(
            organization_id, device.id
        ):
            if violation.policy_id != resolved_policy_id:
                # The policy that produced this violation no longer
                # applies to this device at all (unassigned, or a
                # higher-precedence policy now resolves instead) — see
                # docs/architecture/phase-13.md for why this can't be
                # determined more precisely than "unassigned vs. changed"
                # from inside evaluate_device alone.
                reason = "POLICY_UNASSIGNED" if resolved_policy_id is None else "POLICY_CHANGED"
                await self._violation_repo.update(
                    violation,
                    status=ViolationStatus.RESOLVED,
                    resolved_at=now,
                    resolution_reason=reason,
                )
                continue

            if violation.rule_code not in failed:
                await self._violation_repo.update(
                    violation,
                    status=ViolationStatus.RESOLVED,
                    resolved_at=now,
                    resolution_reason="RULE_PASSED",
                )
                continue

            # Still failing under the same policy. If remediation is
            # in flight, check whether the command it created has reached
            # a terminal state — per docs/architecture/phase-13.md, a
            # command reaching COMPLETED never implies the violation is
            # resolved by itself; only the rule re-passing (handled above,
            # on some LATER evaluation) does that. Reaching ANY terminal
            # state while the rule still fails just means this attempt is
            # over — reopen for a possible next attempt, bounded by
            # max_attempts below.
            if (
                violation.status == ViolationStatus.REMEDIATING
                and violation.last_remediation_command_id is not None
            ):
                command = await self._command_repo.get_for_organization(
                    organization_id, violation.last_remediation_command_id
                )
                if command is not None and command.status in _TERMINAL_COMMAND_STATUSES:
                    violation = await self._violation_repo.update(
                        violation, status=ViolationStatus.OPEN
                    )

            r = failed[violation.rule_code]
            violation = await self._violation_repo.update(
                violation,
                last_seen_at=now,
                message=r.message,
                expected_value=r.expected,
                current_value=r.actual,
                policy_version_id=resolved_version_id,
            )
            handled_codes.add(violation.rule_code)
            if violation.status == ViolationStatus.OPEN:
                newly_open.append(violation)

        if resolved_policy_id is not None:
            for code, r in failed.items():
                if code in handled_codes:
                    continue
                existing = await self._violation_repo.get_by_device_policy_rule(
                    organization_id, device.id, resolved_policy_id, code
                )
                if existing is None:
                    violation = await self._violation_repo.create(
                        organization_id,
                        device_id=device.id,
                        policy_id=resolved_policy_id,
                        policy_version_id=resolved_version_id,
                        rule_code=code,
                        status=ViolationStatus.OPEN,
                        expected_value=r.expected,
                        current_value=r.actual,
                        message=r.message,
                        detected_at=now,
                        last_seen_at=now,
                        remediation_attempts=0,
                    )
                else:
                    # RESOLVED (can't be anything else — an active row for
                    # this exact device+policy+rule would already have
                    # been handled above) — a regression. Reopen as a
                    # fresh occurrence: new detection time, attempt count
                    # reset. Deliberately does NOT reset
                    # automated_remediation_disabled — an admin's earlier
                    # choice to disable automation for this violation
                    # survives a regression, not silently overridden.
                    violation = await self._violation_repo.update(
                        existing,
                        status=ViolationStatus.OPEN,
                        detected_at=now,
                        last_seen_at=now,
                        resolved_at=None,
                        resolution_reason=None,
                        remediation_attempts=0,
                        expected_value=r.expected,
                        current_value=r.actual,
                        message=r.message,
                        policy_version_id=resolved_version_id,
                    )
                newly_open.append(violation)

        for violation in newly_open:
            await self._maybe_trigger_remediation(organization_id, violation, remediation_config)

    async def _maybe_trigger_remediation(
        self,
        organization_id: uuid.UUID,
        violation: PolicyViolation,
        remediation_config: RemediationPolicyConfig | None,
    ) -> None:
        if remediation_config is None or remediation_config.mode != "COMMAND":
            return
        if violation.automated_remediation_disabled:
            return
        if violation.remediation_attempts >= remediation_config.max_attempts:
            if violation.status != ViolationStatus.REMEDIATION_FAILED:
                await self._violation_repo.update(
                    violation, status=ViolationStatus.REMEDIATION_FAILED
                )
            return

        assert remediation_config.command_type is not None  # enforced by the schema validator
        new_attempts = violation.remediation_attempts + 1
        violation = await self._violation_repo.update(
            violation,
            status=ViolationStatus.REMEDIATION_PENDING,
            remediation_attempts=new_attempts,
        )
        # Not audited here — COMPLIANCE_REMEDIATION_TRIGGERED is logged
        # once the COMPLIANCE_REMEDIATION job actually creates the
        # DeviceCommand (app/workers/loop.py::_process_compliance_remediation),
        # matching how DEVICE_COMMAND_CREATED is audited at actual
        # creation, not at some earlier "decided to create one" point.
        await self._job_service.enqueue_compliance_remediation(
            organization_id, violation.id, command_type=remediation_config.command_type
        )

    async def get_device_compliance(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceCompliance:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        compliance = await self._compliance_repo.get_for_device(organization_id, device_id)
        if compliance is None:
            return DeviceCompliance(
                device_id=device_id,
                organization_id=organization_id,
                status=ComplianceStatus.UNKNOWN,
                policy_id=None,
                policy_version_id=None,
                details=[],
                evaluated_at=None,
            )
        return compliance

    async def list_non_compliant(
        self, organization_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[DeviceCompliance], int]:
        items = await self._compliance_repo.list_for_organization(
            organization_id, status=ComplianceStatus.NON_COMPLIANT, offset=offset, limit=limit
        )
        counts = await self._compliance_repo.count_by_status(organization_id)
        return items, counts.get(ComplianceStatus.NON_COMPLIANT.value, 0)

    async def get_summary(self, organization_id: uuid.UUID) -> dict[str, int]:
        total_devices = await self._device_repo.count_for_organization(organization_id)
        counts = await self._compliance_repo.count_by_status(organization_id)
        compliant = counts.get(ComplianceStatus.COMPLIANT.value, 0)
        non_compliant = counts.get(ComplianceStatus.NON_COMPLIANT.value, 0)
        unknown = counts.get(ComplianceStatus.UNKNOWN.value, 0)
        evaluated = compliant + non_compliant + unknown
        return {
            "total_devices": total_devices,
            "compliant": compliant,
            "non_compliant": non_compliant,
            "unknown": unknown,
            "not_evaluated": max(total_devices - evaluated, 0),
        }

    # ------------------------------------------------------------------
    # Violation admin API (Phase 13)
    # ------------------------------------------------------------------

    async def get_violation(
        self, organization_id: uuid.UUID, violation_id: uuid.UUID
    ) -> PolicyViolation:
        violation = await self._violation_repo.get_for_organization(organization_id, violation_id)
        if violation is None:
            raise AppError(
                code="VIOLATION_NOT_FOUND",
                message="Policy violation not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return violation

    async def list_violations(
        self,
        organization_id: uuid.UUID,
        *,
        device_id: uuid.UUID | None = None,
        policy_id: uuid.UUID | None = None,
        violation_status: ViolationStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[PolicyViolation], int]:
        items = await self._violation_repo.list_for_organization(
            organization_id,
            device_id=device_id,
            policy_id=policy_id,
            status=violation_status,
            offset=offset,
            limit=limit,
        )
        total = await self._violation_repo.count_for_organization(
            organization_id, device_id=device_id, policy_id=policy_id, status=violation_status
        )
        return items, total

    async def _current_remediation_config(
        self, organization_id: uuid.UUID, policy_id: uuid.UUID
    ) -> RemediationPolicyConfig | None:
        policy = await self._policy_repo.get_for_organization(organization_id, policy_id)
        if (
            policy is None
            or policy.status != PolicyStatus.ACTIVE
            or policy.current_version_id is None
        ):
            return None
        version = await self._policy_repo.get_version_for_organization(
            organization_id, policy.current_version_id
        )
        if version is None:
            return None
        config = PolicyConfiguration.model_validate(version.configuration)
        return config.remediation

    async def retry_remediation(
        self, organization_id: uuid.UUID, violation_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> PolicyViolation:
        """Manual retry — only from REMEDIATION_FAILED (the "automated
        attempts exhausted, needs a human" state). Deliberately RESETS
        remediation_attempts to 0, unlike Phase 10's job retry (which
        preserves attempts/last_error) — that precedent exists to stop an
        automatic retry loop from becoming an unbounded one; a manual
        retry here is itself the human judgment call that a fresh count is
        warranted, not a mechanism that could loop on its own."""
        violation = await self.get_violation(organization_id, violation_id)
        if violation.status != ViolationStatus.REMEDIATION_FAILED:
            raise AppError(
                code="VIOLATION_NOT_RETRYABLE",
                message=f"Only a violation in REMEDIATION_FAILED can be manually retried "
                f"(this violation is {violation.status.value}).",
                status_code=status.HTTP_409_CONFLICT,
            )
        remediation_config = await self._current_remediation_config(
            organization_id, violation.policy_id
        )
        if remediation_config is None or remediation_config.mode != "COMMAND":
            raise AppError(
                code="REMEDIATION_NOT_CONFIGURED",
                message="This policy no longer configures automated command remediation.",
                status_code=status.HTTP_409_CONFLICT,
            )
        assert remediation_config.command_type is not None
        violation = await self._violation_repo.update(
            violation,
            status=ViolationStatus.REMEDIATION_PENDING,
            remediation_attempts=1,
        )
        await self._job_service.enqueue_compliance_remediation(
            organization_id, violation.id, command_type=remediation_config.command_type
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.COMPLIANCE_REMEDIATION_RETRIED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "violation_id": str(violation.id),
                "device_id": str(violation.device_id),
                "rule_code": violation.rule_code,
            },
        )
        return violation

    async def resolve_violation(
        self, organization_id: uuid.UUID, violation_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> PolicyViolation:
        """Manual resolution — an admin asserting the problem is handled
        (e.g. fixed outside this system, or accepted as a known
        exception). Does not touch DeviceCompliance/ComplianceResult — if
        the underlying rule genuinely still fails, the next evaluation
        reopens this exact violation fresh (see _reconcile_violations),
        it isn't permanently suppressed by a manual resolve."""
        violation = await self.get_violation(organization_id, violation_id)
        if violation.status == ViolationStatus.RESOLVED:
            return violation
        violation = await self._violation_repo.update(
            violation,
            status=ViolationStatus.RESOLVED,
            resolved_at=datetime.now(tz=UTC),
            resolution_reason="MANUALLY_RESOLVED",
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.COMPLIANCE_VIOLATION_RESOLVED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"violation_id": str(violation.id), "device_id": str(violation.device_id)},
        )
        return violation

    async def set_remediation_disabled(
        self,
        organization_id: uuid.UUID,
        violation_id: uuid.UUID,
        *,
        disabled: bool,
        actor_user_id: uuid.UUID,
    ) -> PolicyViolation:
        violation = await self.get_violation(organization_id, violation_id)
        if violation.automated_remediation_disabled == disabled:
            return violation
        violation = await self._violation_repo.update(
            violation, automated_remediation_disabled=disabled
        )
        await self._audit_repo.log_event(
            event_type=(
                AuditEventType.COMPLIANCE_REMEDIATION_DISABLED
                if disabled
                else AuditEventType.COMPLIANCE_REMEDIATION_ENABLED
            ),
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"violation_id": str(violation.id), "device_id": str(violation.device_id)},
        )
        return violation
