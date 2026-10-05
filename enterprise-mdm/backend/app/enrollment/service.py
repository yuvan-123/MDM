"""Enrollment service — token issuance, device self-registration, and the
per-device enrollment approve/reject/revoke workflow.

Constructs no raw SQL/ORM queries — delegates strictly to
EnrollmentTokenRepository, DeviceEnrollmentRepository, and DeviceRepository.

Token lifecycle is evaluated lazily (STEP 2 "have expiration" / STEP 16
"token expiration"): there is no background job flipping ACTIVE -> EXPIRED
on a timer. Instead, `_effective_token_status` computes the true status
from `expires_at`/`use_count` at the moment a token is read or used, and
persists the transition if it has changed — the same approach Phase 4 took
for computed device connectivity state, kept here for tokens for the same
reason: "do not rely only on a manually supplied status field."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status

from app.audit.models import AuditEventType, SecurityEventSeverity, SecurityEventType
from app.core.errors import AppError
from app.devices.enums import (
    DevicePlatform,
    DeviceStatus,
    DeviceType,
    EnrollmentStatus,
    OwnershipType,
)
from app.devices.models import Device
from app.enrollment.identity import generate_device_secret, generate_enrollment_token, hash_token
from app.enrollment.models import (
    DeviceEnrollment,
    DeviceEnrollmentStatus,
    EnrollmentToken,
    EnrollmentTokenStatus,
)
from app.platform.base.provider_names import platform_to_provider_name
from app.platform.registry import PlatformProviderRegistry
from app.provider_events.models import ProviderDeviceLinkSource
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_enrollment_repository import DeviceEnrollmentRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.enrollment_token_repository import EnrollmentTokenRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository


def _effective_token_status(token: EnrollmentToken) -> EnrollmentTokenStatus:
    if token.status in (EnrollmentTokenStatus.REVOKED, EnrollmentTokenStatus.EXPIRED):
        return token.status
    # SQLite (test harness) drops tzinfo on round-trip even for
    # DateTime(timezone=True) columns; Postgres does not. Normalize to
    # UTC before comparing, matching the established fix in
    # app/auth/models.py::Session.is_active.
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if datetime.now(tz=UTC) >= expires_at:
        return EnrollmentTokenStatus.EXPIRED
    if token.use_count >= token.max_uses:
        return EnrollmentTokenStatus.EXHAUSTED
    return EnrollmentTokenStatus.ACTIVE


class EnrollmentService:
    def __init__(
        self,
        token_repo: EnrollmentTokenRepository,
        enrollment_repo: DeviceEnrollmentRepository,
        device_repo: DeviceRepository,
        link_repo: ProviderDeviceLinkRepository,
        audit_repo: AuditRepository,
        registry: PlatformProviderRegistry,
    ) -> None:
        self._token_repo = token_repo
        self._enrollment_repo = enrollment_repo
        self._device_repo = device_repo
        self._link_repo = link_repo
        self._audit_repo = audit_repo
        self._registry = registry

    # ------------------------------------------------------------------
    # Tokens
    # ------------------------------------------------------------------

    async def create_token(
        self,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        label: str | None,
        expires_in_hours: int,
        require_approval: bool,
        max_uses: int,
        target_user_id: uuid.UUID | None,
        allowed_platform: DevicePlatform | None,
        allowed_device_type: DeviceType | None,
        provision_via_provider: bool = False,
    ) -> tuple[EnrollmentToken, str, str | None, str | None]:
        """Returns (token, raw_token, provider_enrollment_value,
        provider_enrollment_qr_code) — the latter two are the real
        provider-minted credential's own secret/QR, present only when
        `provision_via_provider` succeeded, and (like raw_token) never
        persisted anywhere: the caller must hand them to the admin now or
        they are gone."""
        if provision_via_provider and allowed_platform is None:
            raise AppError(
                code="PROVIDER_PROVISIONING_REQUIRES_PLATFORM",
                message="provision_via_provider requires allowed_platform to be set — "
                "the real provider mechanism is platform-specific.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        raw_token, token_hash = generate_enrollment_token()

        provider_enrollment_reference: str | None = None
        provider_enrollment_expires_at: datetime | None = None
        provider_enrollment_value: str | None = None
        provider_enrollment_qr_code: str | None = None

        if provision_via_provider:
            assert allowed_platform is not None  # checked above
            provider = self._registry.get_provider(allowed_platform)
            # Raises PlatformProviderUnavailableError (503) if this
            # provider's real mechanism isn't implemented/configured (Apple
            # today — see app/platform/apple/provider.py) or
            # PlatformCommandRejectedError if the real provider rejects the
            # request. Deliberately not caught here — the caller must see
            # the real reason, not a generic "token creation failed."
            # "default" — not a fresh random policy id — matching the
            # single shared policy every other real-Android code path in
            # this codebase already assumes exists
            # (CommandService/apply_policy's POLICY_SYNC, and Phase 14's
            # app-deployment policy patch both hardcode
            # `enterprises/{id}/policies/default`). A random per-token
            # policy id here was a real bug: the Android Management API
            # requires the referenced policy resource to actually exist
            # before a device can apply it during enrollment, and nothing
            # in this codebase ever created a policy resource under a
            # freshly-generated UUID — every prior phase's own testing
            # only exercised this against the mock provider, so a real
            # device was never available to surface the failure until now.
            credential = await provider.enroll_device(
                policy_id="default", duration_seconds=expires_in_hours * 3600
            )
            provider_enrollment_reference = credential.reference
            provider_enrollment_expires_at = credential.expires_at
            provider_enrollment_value = credential.value
            provider_enrollment_qr_code = credential.qr_code

        token = await self._token_repo.create(
            organization_id,
            token_hash=token_hash,
            label=label,
            status=EnrollmentTokenStatus.ACTIVE,
            require_approval=require_approval,
            max_uses=max_uses,
            use_count=0,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=expires_in_hours),
            created_by=actor_user_id,
            target_user_id=target_user_id,
            allowed_platform=allowed_platform,
            allowed_device_type=allowed_device_type,
            provision_via_provider=provision_via_provider,
            provider_enrollment_reference=provider_enrollment_reference,
            provider_enrollment_expires_at=provider_enrollment_expires_at,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.ENROLLMENT_TOKEN_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "token_id": str(token.id),
                "label": label,
                "provision_via_provider": provision_via_provider,
            },
        )
        return token, raw_token, provider_enrollment_value, provider_enrollment_qr_code

    async def list_tokens(
        self, organization_id: uuid.UUID, *, offset: int = 0, limit: int = 50
    ) -> tuple[list[EnrollmentToken], int]:
        tokens = await self._token_repo.list_for_organization(
            organization_id, offset=offset, limit=limit
        )
        total = await self._token_repo.count_for_organization(organization_id)
        return tokens, total

    async def revoke_token(
        self, organization_id: uuid.UUID, token_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> EnrollmentToken:
        token = await self._token_repo.get_for_organization(organization_id, token_id)
        if token is None:
            raise AppError(
                code="ENROLLMENT_TOKEN_NOT_FOUND",
                message="Enrollment token not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        await self._token_repo.set_status(organization_id, token_id, EnrollmentTokenStatus.REVOKED)
        token.status = EnrollmentTokenStatus.REVOKED
        token.revoked_at = datetime.now(tz=UTC)
        token.revoked_by = actor_user_id

        await self._audit_repo.log_event(
            event_type=AuditEventType.ENROLLMENT_TOKEN_REVOKED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"token_id": str(token.id)},
        )
        return token

    # ------------------------------------------------------------------
    # Device self-registration (unauthenticated — the token IS the proof
    # of authorization, same trust model as OrganizationInvitation)
    # ------------------------------------------------------------------

    async def register_device(
        self,
        *,
        raw_token: str,
        display_name: str,
        platform: DevicePlatform,
        device_type: DeviceType,
        ownership_type: OwnershipType,
        manufacturer: str | None,
        model: str | None,
        os_version: str | None,
        serial_number: str | None,
        device_identifier: str | None,
        client_version: str | None,
        provider_device_id: str | None = None,
        apple_push_token: str | None = None,
    ) -> tuple[Device, DeviceEnrollment, str, bool]:
        token = await self._token_repo.get_by_token_hash(hash_token(raw_token))
        if token is None:
            raise AppError(
                code="INVALID_ENROLLMENT_TOKEN",
                message="Enrollment token is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        effective_status = _effective_token_status(token)
        if effective_status != token.status:
            await self._token_repo.set_status(token.organization_id, token.id, effective_status)
            token.status = effective_status
        if effective_status != EnrollmentTokenStatus.ACTIVE:
            raise AppError(
                code="INVALID_ENROLLMENT_TOKEN",
                message="Enrollment token is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if token.allowed_platform is not None and token.allowed_platform != platform:
            raise AppError(
                code="PLATFORM_NOT_ALLOWED",
                message="This enrollment token does not permit this device platform.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if token.allowed_device_type is not None and token.allowed_device_type != device_type:
            raise AppError(
                code="DEVICE_TYPE_NOT_ALLOWED",
                message="This enrollment token does not permit this device type.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        organization_id = token.organization_id  # NEVER derived from client input

        # --- Phase 11: optional provider identity, supplied by the device itself ---
        provider_name = platform_to_provider_name(platform)
        if apple_push_token is not None and provider_name != "apple":
            raise AppError(
                code="APPLE_PUSH_TOKEN_NOT_APPLICABLE",
                message="apple_push_token may only be supplied when platform is IOS or IPADOS.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if provider_device_id is not None:
            if provider_name is None:
                raise AppError(
                    code="PROVIDER_LINKING_NOT_SUPPORTED",
                    message=f"No platform provider exists for platform {platform.value} — "
                    "provider_device_id cannot be linked.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if await self._link_repo.exists_for_provider(
                organization_id, provider_name, provider_device_id
            ):
                await self._audit_repo.log_security_event(
                    event_type=SecurityEventType.DEVICE_ENROLLMENT_FAILED,
                    severity=SecurityEventSeverity.MEDIUM,
                    details={
                        "organization_id": str(organization_id),
                        "provider_name": provider_name,
                        "provider_device_id": provider_device_id,
                        "reason": "duplicate_provider_device",
                    },
                )
                raise AppError(
                    code="DEVICE_ALREADY_ENROLLED",
                    message="A device is already linked to this provider identity in "
                    "this organization — no duplicate device was created.",
                    status_code=status.HTTP_409_CONFLICT,
                )

        initial_enrollment_status = (
            EnrollmentStatus.PENDING if token.require_approval else EnrollmentStatus.ENROLLED
        )
        now = datetime.now(tz=UTC)

        device = await self._device_repo.create_for_organization(
            organization_id,
            display_name=display_name,
            manufacturer=manufacturer,
            model=model,
            platform=platform,
            operating_system=None,
            os_version=os_version,
            device_type=device_type,
            ownership_type=ownership_type,
            serial_number=serial_number,
            device_identifier=device_identifier,
            status=DeviceStatus.ACTIVE,
            enrollment_status=initial_enrollment_status,
            assigned_user_id=token.target_user_id,
            client_version=client_version,
            last_enrollment_attempt_at=now,
            enrolled_at=now if initial_enrollment_status == EnrollmentStatus.ENROLLED else None,
        )

        raw_secret, secret_hash = generate_device_secret()

        enrollment_status = (
            DeviceEnrollmentStatus.PENDING
            if token.require_approval
            else DeviceEnrollmentStatus.ENROLLED
        )
        enrollment = await self._enrollment_repo.create(
            organization_id,
            enrollment_token_id=token.id,
            device_id=device.id,
            status=enrollment_status,
            expires_at=token.expires_at,
        )

        device = await self._device_repo.update_for_organization(
            device, enrollment_id=enrollment.id, device_secret_hash=secret_hash
        )

        provider_link_created = False
        if provider_device_id is not None:
            assert provider_name is not None  # validated above
            await self._link_repo.create(
                organization_id,
                device_id=device.id,
                provider_name=provider_name,
                provider_device_id=provider_device_id,
                apple_push_token=apple_push_token,
                link_source=ProviderDeviceLinkSource.PROVIDER_ENROLLED,
            )
            provider_link_created = True
            await self._audit_repo.log_event(
                event_type=AuditEventType.PROVIDER_DEVICE_LINK_CREATED,
                organization_id=organization_id,
                metadata={
                    "device_id": str(device.id),
                    "provider_name": provider_name,
                    "source": "enrollment_registration",
                },
            )

        await self._token_repo.increment_use_count(token.id)
        new_use_count = token.use_count + 1
        if new_use_count >= token.max_uses:
            await self._token_repo.set_status(
                organization_id, token.id, EnrollmentTokenStatus.EXHAUSTED
            )

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_ENROLLMENT_REGISTERED,
            organization_id=organization_id,
            metadata={
                "device_id": str(device.id),
                "enrollment_id": str(enrollment.id),
                "token_id": str(token.id),
                "auto_enrolled": not token.require_approval,
                "provider_link_created": provider_link_created,
            },
        )
        return device, enrollment, raw_secret, provider_link_created

    # ------------------------------------------------------------------
    # Approve / reject / revoke (admin actions on a device's enrollment)
    # ------------------------------------------------------------------

    async def _get_enrollment_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> DeviceEnrollment:
        enrollment = await self._enrollment_repo.get_by_device_id(organization_id, device_id)
        if enrollment is None:
            raise AppError(
                code="DEVICE_ENROLLMENT_NOT_FOUND",
                message="This device has no enrollment record (it was created manually).",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return enrollment

    async def approve_enrollment(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> DeviceEnrollment:
        enrollment = await self._get_enrollment_for_device(organization_id, device_id)
        if enrollment.status != DeviceEnrollmentStatus.PENDING:
            raise AppError(
                code="INVALID_ENROLLMENT_TRANSITION",
                message=f"Cannot approve an enrollment in status {enrollment.status.value}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        now = datetime.now(tz=UTC)
        enrollment = await self._enrollment_repo.update_for_organization(
            enrollment,
            status=DeviceEnrollmentStatus.ENROLLED,
            approved_by=actor_user_id,
            approved_at=now,
        )
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is not None:
            await self._device_repo.update_for_organization(
                device, enrollment_status=EnrollmentStatus.ENROLLED, enrolled_at=now
            )

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_ENROLLMENT_APPROVED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            target_user_id=None,
            metadata={"device_id": str(device_id), "enrollment_id": str(enrollment.id)},
        )
        return enrollment

    async def reject_enrollment(
        self,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        reason: str | None,
    ) -> DeviceEnrollment:
        enrollment = await self._get_enrollment_for_device(organization_id, device_id)
        if enrollment.status != DeviceEnrollmentStatus.PENDING:
            raise AppError(
                code="INVALID_ENROLLMENT_TRANSITION",
                message=f"Cannot reject an enrollment in status {enrollment.status.value}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        now = datetime.now(tz=UTC)
        enrollment = await self._enrollment_repo.update_for_organization(
            enrollment,
            status=DeviceEnrollmentStatus.REJECTED,
            rejected_by=actor_user_id,
            rejected_at=now,
            rejection_reason=reason,
        )
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is not None:
            await self._device_repo.update_for_organization(
                device, enrollment_status=EnrollmentStatus.REJECTED
            )

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_ENROLLMENT_REJECTED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "device_id": str(device_id),
                "enrollment_id": str(enrollment.id),
                "reason": reason,
            },
        )
        return enrollment

    async def revoke_enrollment(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> DeviceEnrollment:
        enrollment = await self._get_enrollment_for_device(organization_id, device_id)
        if enrollment.status != DeviceEnrollmentStatus.ENROLLED:
            raise AppError(
                code="INVALID_ENROLLMENT_TRANSITION",
                message=f"Cannot revoke an enrollment in status {enrollment.status.value}.",
                status_code=status.HTTP_409_CONFLICT,
            )
        now = datetime.now(tz=UTC)
        enrollment = await self._enrollment_repo.update_for_organization(
            enrollment,
            status=DeviceEnrollmentStatus.REVOKED,
            revoked_by=actor_user_id,
            revoked_at=now,
        )
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is not None:
            # Immediately invalidate the device's check-in credential — a
            # revoked enrollment must not be able to keep heartbeating.
            await self._device_repo.update_for_organization(
                device,
                enrollment_status=EnrollmentStatus.REVOKED,
                device_secret_hash=None,
            )

        # Phase 11 §20: a revoked enrollment must not leave a stale
        # ProviderDeviceLink around — a physical device re-enrolling later
        # under a fresh token would otherwise resolve straight back to this
        # revoked, no-longer-legitimate Device row instead of getting a new
        # one. The Device row itself (and its full history) is preserved —
        # only the provider association is removed, identical to what
        # Phase 10's manual unlink already does.
        link = await self._link_repo.get_any_link_for_device(organization_id, device_id)
        if link is not None:
            await self._link_repo.delete(link)
            await self._audit_repo.log_event(
                event_type=AuditEventType.PROVIDER_DEVICE_LINK_DELETED,
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                metadata={
                    "link_id": str(link.id),
                    "device_id": str(device_id),
                    "provider_name": link.provider_name,
                    "reason": "enrollment_revoked",
                },
            )

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_ENROLLMENT_REVOKED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"device_id": str(device_id), "enrollment_id": str(enrollment.id)},
        )
        return enrollment

    # ------------------------------------------------------------------
    # Provider check-in auto-enrollment (Phase 11)
    # ------------------------------------------------------------------

    async def resolve_provider_checkin(
        self,
        *,
        provider_name: str,
        provider_device_id: str,
        provider_enrollment_reference: str | None,
        platform: DevicePlatform,
    ) -> Device | None:
        """Called when a provider check-in (app/platform/android/router.py)
        reports a device identity with no existing ProviderDeviceLink.
        Recovers the organization via the EnrollmentToken whose
        `provider_enrollment_reference` matches (see create_token's
        `provision_via_provider`, and app/platform/android/client.py's
        `get_device_resource`, which is how the caller learns this value
        for a real Android device — it's reported back as the device's own
        `enrollmentTokenName`) and, if that token is still valid,
        auto-creates the same Device/DeviceEnrollment/ProviderDeviceLink
        triad `register_device` creates — just triggered by the provider
        telling us about a device instead of the device calling our own
        API directly. This is the real "zero-touch" enrollment path: the
        physical device never talks to this backend at all until this
        moment.

        Returns None for every failure mode — no matching/active token, or
        a link already existing (a benign race, see below). The caller
        (the webhook) records the inbound event as unresolved either way;
        this method never raises for an ordinary "can't resolve" outcome,
        only for a genuine unexpected error.
        """
        if provider_enrollment_reference is None:
            return None
        token = await self._token_repo.get_by_provider_enrollment_reference_unscoped(
            provider_enrollment_reference
        )
        if token is None:
            return None

        effective_status = _effective_token_status(token)
        if effective_status != token.status:
            await self._token_repo.set_status(token.organization_id, token.id, effective_status)
            token.status = effective_status
        if effective_status != EnrollmentTokenStatus.ACTIVE:
            await self._audit_repo.log_security_event(
                event_type=SecurityEventType.DEVICE_ENROLLMENT_FAILED,
                severity=SecurityEventSeverity.MEDIUM,
                details={
                    "organization_id": str(token.organization_id),
                    "provider_name": provider_name,
                    "provider_device_id": provider_device_id,
                    "reason": f"enrollment_token_{effective_status.value.lower()}",
                },
            )
            return None

        organization_id = token.organization_id

        # Defense in depth against a rare concurrent-check-in race (two
        # notifications for the same never-before-seen device arriving
        # before either commits) — the caller already checked this once
        # before calling in, but re-checking here keeps this method safe
        # to call on its own. uq_provider_device_link is the actual
        # structural backstop either way.
        if await self._link_repo.exists_for_provider(
            organization_id, provider_name, provider_device_id
        ):
            return None

        initial_enrollment_status = (
            EnrollmentStatus.PENDING if token.require_approval else EnrollmentStatus.ENROLLED
        )
        now = datetime.now(tz=UTC)
        device = await self._device_repo.create_for_organization(
            organization_id,
            display_name=f"{provider_name.capitalize()} device {provider_device_id[:12]}",
            manufacturer=None,
            model=None,
            platform=platform,
            operating_system=None,
            os_version=None,
            device_type=DeviceType.PHONE,
            ownership_type=OwnershipType.CORPORATE,
            serial_number=None,
            device_identifier=None,
            status=DeviceStatus.ACTIVE,
            enrollment_status=initial_enrollment_status,
            assigned_user_id=token.target_user_id,
            client_version=None,
            last_enrollment_attempt_at=now,
            enrolled_at=now if initial_enrollment_status == EnrollmentStatus.ENROLLED else None,
        )

        enrollment_status = (
            DeviceEnrollmentStatus.PENDING
            if token.require_approval
            else DeviceEnrollmentStatus.ENROLLED
        )
        enrollment = await self._enrollment_repo.create(
            organization_id,
            enrollment_token_id=token.id,
            device_id=device.id,
            status=enrollment_status,
            expires_at=token.expires_at,
        )
        # No device_secret_hash: this device is managed entirely through
        # the real provider channel (Pub/Sub check-ins), never this
        # codebase's own heartbeat credential — there is no "device calls
        # us directly" step in this flow for a bearer secret to protect.
        device = await self._device_repo.update_for_organization(
            device, enrollment_id=enrollment.id
        )

        await self._link_repo.create(
            organization_id,
            device_id=device.id,
            provider_name=provider_name,
            provider_device_id=provider_device_id,
            apple_push_token=None,
            link_source=ProviderDeviceLinkSource.PROVIDER_ENROLLED,
        )

        await self._token_repo.increment_use_count(token.id)
        new_use_count = token.use_count + 1
        if new_use_count >= token.max_uses:
            await self._token_repo.set_status(
                organization_id, token.id, EnrollmentTokenStatus.EXHAUSTED
            )

        await self._audit_repo.log_event(
            event_type=AuditEventType.DEVICE_ENROLLMENT_REGISTERED,
            organization_id=organization_id,
            metadata={
                "device_id": str(device.id),
                "enrollment_id": str(enrollment.id),
                "token_id": str(token.id),
                "auto_enrolled": not token.require_approval,
                "source": "provider_checkin",
                "provider_name": provider_name,
            },
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.PROVIDER_DEVICE_LINK_CREATED,
            organization_id=organization_id,
            metadata={
                "device_id": str(device.id),
                "provider_name": provider_name,
                "source": "provider_checkin",
            },
        )
        return device
