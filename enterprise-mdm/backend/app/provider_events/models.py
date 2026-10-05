"""Inbound platform-provider event ORM models (Phase 7, extended Phase 8).

`ProviderEvent` is the durable, idempotent record of one inbound event from
an Android Management API Pub/Sub push or an Apple MDM check-in — neither
of which this codebase actually receives yet (no real webhook/subscriber
endpoint exists; see app/provider_events/service.py). This table and its
ingestion method exist so that shape is proven out ahead of a later phase
wiring a real HTTP entry point to it.

`ProviderDeviceLink` (Phase 8) is the explicit, tenant-safe mapping a
provider's own device identifier resolves through — see
app/provider_events/service.py and docs/architecture/provider-events.md §3.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class ProviderEventProcessingStatus(str, enum.Enum):
    """PENDING -> PROCESSED (a recognized event_type's handler ran without
        error)
    PENDING -> FAILED (a recognized event_type's handler raised)
    PENDING -> IGNORED (no handler is registered for this event_type — not
        an error; see app/provider_events/handlers.py)
    PROCESSED / FAILED / IGNORED: terminal. Redelivery of an already
        (provider_name, provider_event_id)-deduped event returns the
        original row unchanged — processing never runs twice."""

    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


class ProviderEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "provider_events"
    __table_args__ = (
        UniqueConstraint(
            "provider_name", "provider_event_id", name="uq_provider_event_dedupe"
        ),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="NULL when the caller did not supply an organization context "
        "— device resolution is skipped in that case (see "
        "app/provider_events/service.py), since a provider_device_id is "
        "only unique per organization and resolving blind would be a real "
        "cross-tenant ambiguity.",
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    provider_name: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True, comment='"android" | "apple".'
    )
    provider_event_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="The provider's own event/message identifier — the "
        "(provider_name, provider_event_id) pair is what makes ingestion "
        "idempotent against redelivery.",
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Free-form provider-defined event type string, not an enum — "
        "no real payload shape has been observed yet (see module docstring). "
        "Matched against app/provider_events/handlers.py's registry by value.",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONType,
        nullable=False,
        comment="Raw provider payload. Must NEVER contain provider credentials.",
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_status: Mapped[ProviderEventProcessingStatus] = mapped_column(
        Enum(ProviderEventProcessingStatus, name="provider_event_processing_status"),
        nullable=False,
        default=ProviderEventProcessingStatus.PENDING,
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ProviderEvent id={self.id} provider={self.provider_name} "
            f"type={self.event_type} status={self.processing_status}>"
        )


class ProviderDeviceLinkSource(str, enum.Enum):
    """MANUAL: created through the Phase 10 admin API (or directly against
    the database) by a human, not produced by any enrollment flow this
    codebase runs. PROVIDER_ENROLLED: would mean the link was produced by
    genuine platform enrollment (real Android Enterprise / Apple ADE) —
    reserved for a later phase; nothing in this codebase creates a
    PROVIDER_ENROLLED row today, since no real enrollment flow exists (see
    docs/architecture/phase-9.md §6). Kept as a real enum value now, not
    added later, so the eventual distinction doesn't require another
    migration on top of this one."""

    MANUAL = "MANUAL"
    PROVIDER_ENROLLED = "PROVIDER_ENROLLED"


class ProviderDeviceLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Explicit (organization, provider, provider_device_id) -> device
    mapping (Phase 8) — the preferred resolution model over guessing from
    `Device.device_identifier` (Phase 7's approach, now superseded): a
    provider's own device identifier is a distinct concept from the
    enrollment-time identifier Phase 5 captures, and conflating them was
    honest-but-imprecise. `provider_device_id` is NOT assumed globally
    unique — the unique constraint below is scoped to
    (organization_id, provider_name, provider_device_id), matching the only
    uniqueness guarantee this codebase can actually claim.

    Phase 10: `apple_push_token` is a second, genuinely distinct identifier
    for Apple rows only — see the column's own comment for why one string
    column was never enough for Apple specifically. `provider_device_id`
    now unambiguously means "the identifier a device authenticates itself
    with" for every provider (Android's device resource id; Apple's UDID),
    with `apple_push_token` carrying the separate, rotatable identifier
    outbound dispatch actually needs for Apple. Phase 9's check-in handler
    had a real bug here — TokenUpdate overwrote `provider_device_id` with
    the push token, corrupting the UDID-based resolution key after the
    first token update; fixed in the same phase this column was added
    (`app/platform/apple/router.py`).

    Populated only by an authorized admin through the Phase 10 CRUD API
    today (`link_source=MANUAL`) — no real Android/Apple enrollment exists
    (see docs/architecture/device-commands.md §7 and
    docs/architecture/provider-events.md §4), so nothing in this codebase
    can create a `PROVIDER_ENROLLED` row until a later phase performs real
    platform enrollment.
    """

    __tablename__ = "provider_device_links"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider_name",
            "provider_device_id",
            name="uq_provider_device_link",
        ),
        Index("ix_provider_device_links_device", "device_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_device_id: Mapped[str] = mapped_column(String(255), nullable=False)
    apple_push_token: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Apple rows only — the device's current APNs push token, "
        "updated by TokenUpdate check-in messages "
        "(app/platform/apple/router.py). Distinct from provider_device_id "
        "(the UDID) because Apple's protocol genuinely has two identifiers: "
        "a stable device identity and a separate, rotatable push token used "
        "only for the outbound wake-up notification. Always NULL for "
        "non-Apple rows.",
    )
    link_source: Mapped[ProviderDeviceLinkSource] = mapped_column(
        Enum(ProviderDeviceLinkSource, name="provider_device_link_source"),
        nullable=False,
        default=ProviderDeviceLinkSource.MANUAL,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ProviderDeviceLink org={self.organization_id} "
            f"provider={self.provider_name} device={self.device_id}>"
        )
