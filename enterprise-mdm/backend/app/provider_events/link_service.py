"""Provider-device-link administration service (Phase 10).

Removes the Phase 9 operational limitation ("nothing but a direct
database write can populate ProviderDeviceLink") — an authorized
administrator can now create/inspect/update/delete these mappings through
a real API. Every link created here is `link_source=MANUAL`; nothing in
this codebase can create a `PROVIDER_ENROLLED` row (see
docs/architecture/phase-9.md §6 / phase-10.md for why).

Constructs no raw SQL/ORM queries — delegates strictly to
ProviderDeviceLinkRepository, DeviceRepository, and AuditRepository.
"""

from __future__ import annotations

import uuid

from fastapi import status

from app.audit.models import AuditEventType
from app.core.errors import AppError
from app.platform.base.provider_names import VALID_PROVIDER_NAMES
from app.provider_events.models import ProviderDeviceLink, ProviderDeviceLinkSource
from app.repositories.audit_repository import AuditRepository
from app.repositories.device_repository import DeviceRepository
from app.repositories.provider_device_link_repository import ProviderDeviceLinkRepository


def _not_found() -> AppError:
    return AppError(
        code="PROVIDER_DEVICE_LINK_NOT_FOUND",
        message="Provider device link not found.",
        status_code=status.HTTP_404_NOT_FOUND,
    )


class ProviderDeviceLinkService:
    def __init__(
        self,
        link_repo: ProviderDeviceLinkRepository,
        device_repo: DeviceRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._link_repo = link_repo
        self._device_repo = device_repo
        self._audit_repo = audit_repo

    async def get_link(
        self, organization_id: uuid.UUID, link_id: uuid.UUID
    ) -> ProviderDeviceLink:
        link = await self._link_repo.get_for_organization(organization_id, link_id)
        if link is None:
            raise _not_found()
        return link

    async def list_links(
        self,
        organization_id: uuid.UUID,
        *,
        provider_name: str | None = None,
        device_id: uuid.UUID | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ProviderDeviceLink], int]:
        links = await self._link_repo.list_for_organization(
            organization_id,
            provider_name=provider_name,
            device_id=device_id,
            search=search,
            offset=offset,
            limit=limit,
        )
        total = await self._link_repo.count_for_organization(
            organization_id, provider_name=provider_name, device_id=device_id, search=search
        )
        return links, total

    async def create_link(
        self,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        device_id: uuid.UUID,
        provider_name: str,
        provider_device_id: str,
        apple_push_token: str | None,
    ) -> ProviderDeviceLink:
        if provider_name not in VALID_PROVIDER_NAMES:
            raise AppError(
                code="PROVIDER_DEVICE_LINK_INVALID_PROVIDER",
                message=f"Unrecognized provider_name {provider_name!r} — expected one of "
                f"{sorted(VALID_PROVIDER_NAMES)}.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Tenant safety: a device_id that exists but belongs to a different
        # organization must resolve identically to one that doesn't exist
        # at all — never confirm cross-tenant existence, never let
        # Organization A create a mapping pointing at Organization B's
        # device (Phase 10 §5/§20).
        device = await self._device_repo.get_for_organization(organization_id, device_id)
        if device is None:
            raise AppError(
                code="DEVICE_NOT_FOUND",
                message="Device not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if await self._link_repo.exists_for_provider(
            organization_id, provider_name, provider_device_id
        ):
            raise AppError(
                code="PROVIDER_DEVICE_LINK_ALREADY_EXISTS",
                message="A link already exists for this organization, provider, and "
                "provider_device_id — structurally enforced by "
                "uq_provider_device_link, not just this check.",
                status_code=status.HTTP_409_CONFLICT,
            )

        link = await self._link_repo.create(
            organization_id,
            device_id=device_id,
            provider_name=provider_name,
            provider_device_id=provider_device_id,
            apple_push_token=apple_push_token,
            link_source=ProviderDeviceLinkSource.MANUAL,
        )
        await self._audit_repo.log_event(
            event_type=AuditEventType.PROVIDER_DEVICE_LINK_CREATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "link_id": str(link.id),
                "device_id": str(device_id),
                "provider_name": provider_name,
            },
        )
        return link

    async def update_link(
        self,
        organization_id: uuid.UUID,
        link_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID,
        provider_device_id: str | None,
        apple_push_token: str | None,
    ) -> ProviderDeviceLink:
        link = await self.get_link(organization_id, link_id)

        fields: dict[str, object] = {}
        if provider_device_id is not None and provider_device_id != link.provider_device_id:
            if await self._link_repo.exists_for_provider(
                organization_id, link.provider_name, provider_device_id
            ):
                raise AppError(
                    code="PROVIDER_DEVICE_LINK_ALREADY_EXISTS",
                    message="Another link already uses this provider_device_id for this "
                    "organization and provider.",
                    status_code=status.HTTP_409_CONFLICT,
                )
            fields["provider_device_id"] = provider_device_id
        if apple_push_token is not None:
            fields["apple_push_token"] = apple_push_token

        if not fields:
            return link

        link = await self._link_repo.update(link, **fields)
        await self._audit_repo.log_event(
            event_type=AuditEventType.PROVIDER_DEVICE_LINK_UPDATED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={"link_id": str(link.id), "fields": list(fields.keys())},
        )
        return link

    async def delete_link(
        self, organization_id: uuid.UUID, link_id: uuid.UUID, *, actor_user_id: uuid.UUID
    ) -> None:
        """Removes only the provider association. The internal Device row
        is never touched — it remains a full MDM object, just no longer
        addressable through this provider mapping (Phase 10 §7)."""
        link = await self.get_link(organization_id, link_id)
        device_id = link.device_id
        provider_name = link.provider_name
        await self._link_repo.delete(link)
        await self._audit_repo.log_event(
            event_type=AuditEventType.PROVIDER_DEVICE_LINK_DELETED,
            actor_user_id=actor_user_id,
            organization_id=organization_id,
            metadata={
                "link_id": str(link_id),
                "device_id": str(device_id),
                "provider_name": provider_name,
            },
        )
