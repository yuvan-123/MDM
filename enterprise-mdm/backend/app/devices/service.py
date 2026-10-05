"""Device service — inventory CRUD, assignment, and lifecycle business rules.

Constructs no raw SQL/ORM queries — delegates strictly to DeviceRepository,
MembershipRepository, and UserRepository (matches OrganizationService /
MembershipService in app/organizations/).

Lifecycle (STEP 9): only ACTIVE/INACTIVE/LOST are reachable through the
generic update_device() call; BLOCKED and RETIRED are reachable only
through block_device()/retire_device(), each behind its own permission
(device.block / device.retire), and RETIRED is terminal — nothing
transitions a device out of RETIRED, ever, matching the spec's explicit
"RETIRED -> ACTIVE should NOT be allowed through a normal PATCH".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.devices.enums import (
    DevicePlatform,
    DeviceStatus,
    DeviceType,
    EnrollmentStatus,
    OwnershipType,
)
from app.devices.identity import hash_identifier, last_chars
from app.devices.models import Device
from app.organizations.models import MembershipStatus
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.membership_repository import MembershipRepository
from app.repositories.user_repository import UserRepository

# Status graph for the *generic* update_device() path only.
_GENERIC_TRANSITIONS: dict[DeviceStatus, frozenset[DeviceStatus]] = {
    DeviceStatus.ACTIVE: frozenset({DeviceStatus.INACTIVE, DeviceStatus.LOST}),
    DeviceStatus.INACTIVE: frozenset({DeviceStatus.ACTIVE, DeviceStatus.LOST}),
    DeviceStatus.LOST: frozenset({DeviceStatus.ACTIVE, DeviceStatus.INACTIVE}),
    DeviceStatus.BLOCKED: frozenset(),
    DeviceStatus.RETIRED: frozenset(),
}
# Statuses block_device() may transition out of.
_BLOCKABLE_FROM = frozenset({DeviceStatus.ACTIVE, DeviceStatus.INACTIVE, DeviceStatus.LOST})
# Statuses retire_device() may transition out of (everything but RETIRED itself).
_RETIRABLE_FROM = frozenset(
    {DeviceStatus.ACTIVE, DeviceStatus.INACTIVE, DeviceStatus.LOST, DeviceStatus.BLOCKED}
)


def _not_found() -> AppError:
    return AppError(
        code="DEVICE_NOT_FOUND",
        message="Device not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


class DeviceService:
    def __init__(
        self,
        device_repo: DeviceRepository,
        membership_repo: MembershipRepository,
        user_repo: UserRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._device_repo = device_repo
        self._membership_repo = membership_repo
        self._user_repo = user_repo
        self._audit_repo = audit_repo

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_device(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        view_sensitive: bool,
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()

        if view_sensitive and (device.imei_last4 or device.wifi_mac_last4):
            await self._audit_repo.log_event(
                event_type=AuditEventType.DEVICE_SENSITIVE_DATA_VIEWED,
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                metadata={"device_id": str(device.id)},
            )
        return device

    async def list_devices(
        self,
        organization_id: uuid.UUID,
        *,
        status: DeviceStatus | None = None,
        platform: DevicePlatform | None = None,
        ownership_type: OwnershipType | None = None,
        enrollment_status: EnrollmentStatus | None = None,
        assigned_user_id: uuid.UUID | None = None,
        provider_name: str | None = None,
        search: str | None = None,
        sort: str = "created_at",
        sort_desc: bool = True,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[Sequence[Device], int]:
        devices = await self._device_repo.list_for_organization(
            organization_id,
            status=status,
            platform=platform,
            ownership_type=ownership_type,
            enrollment_status=enrollment_status,
            assigned_user_id=assigned_user_id,
            provider_name=provider_name,
            search=search,
            sort=sort,
            sort_desc=sort_desc,
            offset=offset,
            limit=limit,
        )
        total = await self._device_repo.count_for_organization(
            organization_id,
            status=status,
            platform=platform,
            ownership_type=ownership_type,
            enrollment_status=enrollment_status,
            assigned_user_id=assigned_user_id,
            provider_name=provider_name,
            search=search,
        )
        return devices, total

    # ------------------------------------------------------------------
    # Create / Update
    # ------------------------------------------------------------------

    async def _check_duplicates(
        self,
        organization_id: uuid.UUID,
        *,
        device_identifier: str | None,
        serial_number: str | None,
        imei_hash: str | None,
    ) -> None:
        if device_identifier and await self._device_repo.exists_device_identifier(
            organization_id, device_identifier
        ):
            raise AppError(
                code="DEVICE_ALREADY_EXISTS",
                message="A device with this device_identifier already exists in this organization.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if serial_number and await self._device_repo.exists_serial_number(
            organization_id, serial_number
        ):
            raise AppError(
                code="DEVICE_ALREADY_EXISTS",
                message="A device with this serial_number already exists in this organization.",
                status_code=status.HTTP_409_CONFLICT,
            )
        if imei_hash and await self._device_repo.exists_imei_hash(organization_id, imei_hash):
            raise AppError(
                code="DEVICE_ALREADY_EXISTS",
                message="A device with this IMEI already exists in this organization.",
                status_code=status.HTTP_409_CONFLICT,
            )

    async def create_device(
        self,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        display_name: str,
        platform: DevicePlatform,
        device_type: DeviceType,
        ownership_type: OwnershipType,
        manufacturer: str | None = None,
        model: str | None = None,
        model_identifier: str | None = None,
        operating_system: str | None = None,
        os_version: str | None = None,
        serial_number: str | None = None,
        device_identifier: str | None = None,
        imei: str | None = None,
        wifi_mac: str | None = None,
    ) -> Device:
        imei_hash = hash_identifier(imei) if imei else None
        imei_last4 = last_chars(imei) if imei else None
        wifi_mac_hash = hash_identifier(wifi_mac) if wifi_mac else None
        wifi_mac_last4 = last_chars(wifi_mac) if wifi_mac else None

        await self._check_duplicates(
            organization_id,
            device_identifier=device_identifier,
            serial_number=serial_number,
            imei_hash=imei_hash,
        )

        device = await self._device_repo.create_for_organization(
            organization_id,
            display_name=display_name,
            manufacturer=manufacturer,
            model=model,
            model_identifier=model_identifier,
            platform=platform,
            operating_system=operating_system,
            os_version=os_version,
            device_type=device_type,
            ownership_type=ownership_type,
            serial_number=serial_number,
            device_identifier=device_identifier,
            imei_hash=imei_hash,
            imei_last4=imei_last4,
            wifi_mac_hash=wifi_mac_hash,
            wifi_mac_last4=wifi_mac_last4,
            status=DeviceStatus.ACTIVE,
            enrollment_status=EnrollmentStatus.NOT_ENROLLED,
        )

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "device_id": str(device.id),
                "platform": platform.value,
                "device_type": device_type.value,
            },
        )
        return device

    async def update_device(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        **fields: object,
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()

        clean_fields = {k: v for k, v in fields.items() if v is not None}
        if not clean_fields:
            return device

        new_status_raw = clean_fields.get("status")
        new_status = new_status_raw if isinstance(new_status_raw, DeviceStatus) else None
        if new_status is not None and new_status != device.status:
            if new_status not in _GENERIC_TRANSITIONS.get(device.status, frozenset()):
                raise AppError(
                    code="INVALID_LIFECYCLE_TRANSITION",
                    message=(
                        f"Cannot transition device from {device.status.value} "
                        f"to {new_status.value}."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                )
        elif new_status is not None:
            clean_fields.pop("status")

        device_identifier = clean_fields.get("device_identifier")
        serial_number = clean_fields.get("serial_number")
        if device_identifier and device_identifier != device.device_identifier:
            if await self._device_repo.exists_device_identifier(
                organization_id, device_identifier  # type: ignore[arg-type]
            ):
                raise AppError(
                    code="DEVICE_ALREADY_EXISTS",
                    message="A device with this device_identifier already exists "
                    "in this organization.",
                    status_code=status.HTTP_409_CONFLICT,
                )
        if serial_number and serial_number != device.serial_number:
            if await self._device_repo.exists_serial_number(
                organization_id, serial_number  # type: ignore[arg-type]
            ):
                raise AppError(
                    code="DEVICE_ALREADY_EXISTS",
                    message="A device with this serial_number already exists in this organization.",
                    status_code=status.HTTP_409_CONFLICT,
                )

        device = await self._device_repo.update_for_organization(device, **clean_fields)

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_UPDATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device.id), "fields": list(clean_fields.keys())},
        )
        return device

    # ------------------------------------------------------------------
    # Assignment
    # ------------------------------------------------------------------

    async def assign_device(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()
        if device.status == DeviceStatus.RETIRED:
            raise AppError(
                code="DEVICE_RETIRED",
                message="A retired device cannot be assigned.",
                status_code=status.HTTP_409_CONFLICT,
            )

        membership = await self._membership_repo.get_by_user_and_org(user_id, organization_id)
        if membership is None:
            raise AppError(
                code="USER_NOT_IN_ORGANIZATION",
                message="The target user is not a member of this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if membership.status != MembershipStatus.ACTIVE:
            raise AppError(
                code="MEMBERSHIP_NOT_ACTIVE",
                message="Devices can only be assigned to an active member of the organization.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await self._device_repo.assign_for_organization(organization_id, device_id, user_id)
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        assert device is not None

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_ASSIGNED,
            actor_user_id=actor_user_id,
            target_user_id=user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device.id)},
        )
        return device

    async def unassign_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()

        previous_user_id = device.assigned_user_id
        await self._device_repo.unassign_for_organization(organization_id, device_id)
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        assert device is not None

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_UNASSIGNED,
            actor_user_id=actor_user_id,
            target_user_id=previous_user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device.id)},
        )
        return device

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _apply_status_change(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        new_status: DeviceStatus,
        actor_user_id: uuid.UUID,
        event_type: AuditEventType,
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()

        await self._device_repo.set_status(organization_id, device_id, new_status)
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        assert device is not None

        await self._audit_repo.log_event(
            event_type=event_type,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device.id), "new_status": new_status.value},
        )
        return device

    async def block_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()
        if device.status not in _BLOCKABLE_FROM:
            raise AppError(
                code="INVALID_LIFECYCLE_TRANSITION",
                message=f"Cannot block a device in status {device.status.value}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        return await self._apply_status_change(
            organization_id,
            device_id,
            new_status=DeviceStatus.BLOCKED,
            actor_user_id=actor_user_id,
            event_type=AuditEventType.DEVICE_BLOCKED,
        )

    async def unblock_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()
        if device.status != DeviceStatus.BLOCKED:
            raise AppError(
                code="INVALID_LIFECYCLE_TRANSITION",
                message="Only a blocked device can be unblocked.",
                status_code=status.HTTP_409_CONFLICT,
            )
        return await self._apply_status_change(
            organization_id,
            device_id,
            new_status=DeviceStatus.ACTIVE,
            actor_user_id=actor_user_id,
            event_type=AuditEventType.DEVICE_UNBLOCKED,
        )

    async def retire_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> Device:
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()
        if device.status not in _RETIRABLE_FROM:
            raise AppError(
                code="INVALID_LIFECYCLE_TRANSITION",
                message="This device is already retired.",
                status_code=status.HTTP_409_CONFLICT,
            )
        return await self._apply_status_change(
            organization_id,
            device_id,
            new_status=DeviceStatus.RETIRED,
            actor_user_id=actor_user_id,
            event_type=AuditEventType.DEVICE_RETIRED,
        )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> None:
        """Hard delete. Restricted (device.delete), audited. Prefer
        retire_device() for normal operational decommissioning — see
        docs/architecture/device-management.md STEP 22."""
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise _not_found()

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_DELETED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device.id), "display_name": device.display_name},
        )
        await self._device_repo.delete_for_organization(organization_id, device)

    # ------------------------------------------------------------------
    # Heartbeat (Phase 5)
    # ------------------------------------------------------------------

    async def record_heartbeat(
        self,
        device: Device,
        *,
        client_version: str | None,
        os_version: str | None,
        security_patch_level: str | None,
        is_encrypted: bool | None,
        has_screen_lock: bool | None,
        is_rooted: bool | None,
    ) -> Device:
        """Update `last_seen_at`/`last_check_in_at` plus any device-reported
        health fields that were actually provided. Not audited — heartbeats
        are far too frequent to log individually without flooding the
        audit trail (matches the list-view sensitive-data exclusion in
        get_device: routine, high-volume reads/pings are not audit events).
        `device` is the already-authenticated row from
        app.enrollment.dependencies.get_enrolled_device — no organization_id
        or permission check happens here, only field updates."""
        now = datetime.now(tz=UTC)
        fields: dict[str, object] = {"last_seen_at": now, "last_check_in_at": now}
        if client_version is not None:
            fields["client_version"] = client_version
        if os_version is not None:
            fields["os_version"] = os_version
        if security_patch_level is not None:
            fields["security_patch_level"] = security_patch_level
        if is_encrypted is not None:
            fields["is_encrypted"] = is_encrypted
        if has_screen_lock is not None:
            fields["has_screen_lock"] = has_screen_lock
        if is_rooted is not None:
            fields["is_rooted"] = is_rooted

        return await self._device_repo.update_for_organization(device, **fields)
