"""ProviderDeviceLink repository — the ONLY layer that constructs
SQLAlchemy queries for this model. See app/provider_events/models.py for
why this table exists instead of resolving through Device.device_identifier."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.provider_events.models import ProviderDeviceLink


class AmbiguousProviderDeviceLinkError(Exception):
    """Raised by `get_by_provider_device_id_unscoped` when more than one
    organization has linked the same (provider_name, provider_device_id)
    pair — see that method's docstring. Deliberately NOT an AppError
    subclass: this can only be raised from a system-level webhook path,
    never from an admin-facing request, so it has no natural HTTP status
    of its own — callers decide how to represent it (Phase 9 §12: fail
    safely, never pick one)."""

    def __init__(self, provider_name: str, provider_device_id: str) -> None:
        self.provider_name = provider_name
        self.provider_device_id = provider_device_id
        super().__init__(
            f"Ambiguous provider_device_id {provider_device_id!r} for provider "
            f"{provider_name!r}: matched more than one organization."
        )


class ProviderDeviceLinkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_device_id(
        self, organization_id: uuid.UUID, provider_name: str, provider_device_id: str
    ) -> uuid.UUID | None:
        """At most one row can ever match — enforced by
        `uq_provider_device_link`, not by caller discipline — so there is no
        "pick the first match" ambiguity to guard against here; an
        ambiguous resolution is structurally impossible rather than merely
        avoided."""
        result = await self._session.execute(
            select(ProviderDeviceLink.device_id).where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.provider_name == provider_name,
                ProviderDeviceLink.provider_device_id == provider_device_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_provider_device_id(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, provider_name: str
    ) -> str | None:
        """Phase 9: the reverse direction of `get_device_id` — used by
        `CommandService._dispatch` to populate `ProviderDeviceRef.provider_device_id`
        before calling out to a real provider. Org-scoped for the same
        tenant-isolation reason every other lookup here is."""
        result = await self._session.execute(
            select(ProviderDeviceLink.provider_device_id).where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.device_id == device_id,
                ProviderDeviceLink.provider_name == provider_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_apple_push_token(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> str | None:
        """Phase 10: the Apple-specific counterpart to
        `get_provider_device_id` — used by `CommandService._dispatch`
        instead of that method for `provider_name == "apple"`, since
        Apple's outbound dispatch identifier (the APNs push token) is a
        genuinely different value from `provider_device_id` (the UDID).
        See `ProviderDeviceLink.apple_push_token`'s own comment."""
        result = await self._session.execute(
            select(ProviderDeviceLink.apple_push_token).where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.device_id == device_id,
                ProviderDeviceLink.provider_name == "apple",
            )
        )
        return result.scalar_one_or_none()

    async def get_link_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID, provider_name: str
    ) -> ProviderDeviceLink | None:
        result = await self._session.execute(
            select(ProviderDeviceLink).where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.device_id == device_id,
                ProviderDeviceLink.provider_name == provider_name,
            )
        )
        return result.scalar_one_or_none()

    async def get_links_for_devices(
        self, organization_id: uuid.UUID, device_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ProviderDeviceLink]:
        """Phase 12: bulk counterpart to `get_any_link_for_device` — used
        by `GET /devices` (app/devices/router.py) to show each row's
        management state without an N+1 query per page. A device only
        ever has one platform and thus at most one provider link, so this
        is a plain `device_id -> link` map, no aggregation needed."""
        if not device_ids:
            return {}
        result = await self._session.execute(
            select(ProviderDeviceLink).where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.device_id.in_(device_ids),
            )
        )
        return {link.device_id: link for link in result.scalars().all()}

    async def get_any_link_for_device(
        self, organization_id: uuid.UUID, device_id: uuid.UUID
    ) -> ProviderDeviceLink | None:
        """Phase 11: like `get_link_for_device`, but without a
        `provider_name` filter — used where the caller wants to know "does
        this device have a provider link at all" (enrollment-detail
        display; enrollment revocation cleanup) without needing to already
        know which provider. A device only ever has one platform and thus
        at most one provider, so this is never ambiguous in practice, but
        it's still org-scoped for the same tenant-isolation reason every
        other lookup here is."""
        result = await self._session.execute(
            select(ProviderDeviceLink).where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.device_id == device_id,
            )
        )
        return result.scalars().first()

    async def get_by_provider_device_id_unscoped(
        self, provider_name: str, provider_device_id: str
    ) -> ProviderDeviceLink | None:
        """System-only, cross-tenant lookup for inbound webhook resolution
        where the caller genuinely has no organization context yet — e.g.
        Android Pub/Sub notifications, which are scoped to one Google Cloud
        enterprise per *deployment* (a single `Settings.android_enterprise_id`),
        not one per organization (see docs/architecture/phase-9.md §3). Safe
        only because this deployment's architecture assumes exactly one
        Android enterprise, so (provider_name, provider_device_id) is
        expected to be globally unique in practice — the same category of
        exception as DeviceRepository.get_by_id_unscoped, never called from
        an admin-facing code path.

        If more than one organization has somehow linked the same pair
        (only possible through operator error outside this codebase, e.g.
        misconfiguring two organizations against overlapping device-ID
        ranges), this raises `AmbiguousProviderDeviceLinkError` rather than
        returning one of them — an ambiguous resolution must fail safely,
        never guess (Phase 9 §12)."""
        result = await self._session.execute(
            select(ProviderDeviceLink).where(
                ProviderDeviceLink.provider_name == provider_name,
                ProviderDeviceLink.provider_device_id == provider_device_id,
            )
        )
        rows = result.scalars().all()
        if len(rows) > 1:
            raise AmbiguousProviderDeviceLinkError(provider_name, provider_device_id)
        return rows[0] if rows else None

    async def create(self, organization_id: uuid.UUID, **fields: object) -> ProviderDeviceLink:
        link = ProviderDeviceLink(organization_id=organization_id, **fields)
        self._session.add(link)
        await self._session.flush()
        await self._session.refresh(link)
        return link

    async def update(self, link: ProviderDeviceLink, **fields: object) -> ProviderDeviceLink:
        for key, value in fields.items():
            setattr(link, key, value)
        await self._session.flush()
        await self._session.refresh(link)
        return link

    # ------------------------------------------------------------------
    # Admin CRUD (Phase 10)
    # ------------------------------------------------------------------

    async def get_for_organization(
        self, organization_id: uuid.UUID, link_id: uuid.UUID
    ) -> ProviderDeviceLink | None:
        result = await self._session.execute(
            select(ProviderDeviceLink).where(
                ProviderDeviceLink.id == link_id,
                ProviderDeviceLink.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        provider_name: str | None = None,
        device_id: uuid.UUID | None = None,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ProviderDeviceLink]:
        query = select(ProviderDeviceLink).where(
            ProviderDeviceLink.organization_id == organization_id
        )
        if provider_name is not None:
            query = query.where(ProviderDeviceLink.provider_name == provider_name)
        if device_id is not None:
            query = query.where(ProviderDeviceLink.device_id == device_id)
        if search:
            query = query.where(ProviderDeviceLink.provider_device_id.ilike(f"%{search.strip()}%"))
        result = await self._session.execute(
            query.order_by(ProviderDeviceLink.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all())

    async def count_for_organization(
        self,
        organization_id: uuid.UUID,
        *,
        provider_name: str | None = None,
        device_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> int:
        query = (
            select(func.count())
            .select_from(ProviderDeviceLink)
            .where(ProviderDeviceLink.organization_id == organization_id)
        )
        if provider_name is not None:
            query = query.where(ProviderDeviceLink.provider_name == provider_name)
        if device_id is not None:
            query = query.where(ProviderDeviceLink.device_id == device_id)
        if search:
            query = query.where(ProviderDeviceLink.provider_device_id.ilike(f"%{search.strip()}%"))
        result = await self._session.execute(query)
        return int(result.scalar_one())

    async def exists_for_provider(
        self, organization_id: uuid.UUID, provider_name: str, provider_device_id: str
    ) -> bool:
        result = await self._session.execute(
            select(func.count())
            .select_from(ProviderDeviceLink)
            .where(
                ProviderDeviceLink.organization_id == organization_id,
                ProviderDeviceLink.provider_name == provider_name,
                ProviderDeviceLink.provider_device_id == provider_device_id,
            )
        )
        return int(result.scalar_one()) > 0

    async def delete(self, link: ProviderDeviceLink) -> None:
        """Removes only the provider association — never touches the
        internal Device row (Phase 10 §7: unlinking must not delete the
        MDM device object)."""
        await self._session.delete(link)
        await self._session.flush()
