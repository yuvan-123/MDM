"""Remote device command ORM model (Phase 6).

`DeviceCommand` is the persistent, auditable record of one remote action
(lock/reboot/wipe/refresh-info/policy-sync) dispatched to an enrolled
device through a `PlatformManagementProvider`
(app/platform/base/provider.py). One row per command — never mutated back
to a prior state, only forward through the state machine documented on
`DeviceCommandStatus` below. See docs/architecture/device-commands.md for
the full state machine, idempotency, and expiration model.

Like every Phase 4/5 domain table, `organization_id` is mandatory and
indexed — there is no bare `get_by_id` for this model, only
organization-scoped lookups (see app/repositories/device_command_repository.py).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

# Dialect-compatible JSON type: Postgres JSONB in production, JSON in SQLite tests.
JSONType = JSON().with_variant(JSONB, "postgresql")


class DeviceCommandType(str, enum.Enum):
    LOCK = "LOCK"
    REBOOT = "REBOOT"
    WIPE = "WIPE"
    REFRESH_INFO = "REFRESH_INFO"
    POLICY_SYNC = "POLICY_SYNC"
    UNENROLL = "UNENROLL"
    # Phase 14: app deployment is dispatched through this exact same
    # DeviceCommand/CommandService machinery — see
    # app/applications/service.py and app/platform/base/provider.py's
    # install_application/remove_application.
    INSTALL_APPLICATION = "INSTALL_APPLICATION"
    UNINSTALL_APPLICATION = "UNINSTALL_APPLICATION"


class DeviceCommandStatus(str, enum.Enum):
    """See docs/architecture/device-commands.md for the full transition
    diagram. Summary:

    PENDING  -> SENT        (dispatch accepted by the provider)
    PENDING  -> FAILED      (dispatch itself errored: provider unavailable,
                              validation failure)
    SENT     -> COMPLETED   (provider reported COMPLETED — always true for
                              MockManagementProvider; only for a genuinely
                              synchronous real-provider read, e.g. Android
                              REFRESH_INFO)
    SENT     -> FAILED      (provider reported FAILED)
    SENT     -> CANCELLED   (admin cancels — server-side tracking only; see
                              CommandService.cancel_command)
    SENT     -> EXPIRED     (lazy, evaluated at read time once now >= expires_at)
    COMPLETED / FAILED / CANCELLED / EXPIRED: terminal.

    PENDING is retained as a real, briefly-observed row state for
    correctness/auditability even though dispatch happens synchronously
    within the same request that creates the row — there is no
    worker/queue in this codebase that would leave a row sitting in
    PENDING for any meaningful duration.
    """

    PENDING = "PENDING"
    SENT = "SENT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class DeviceCommand(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_commands"
    __table_args__ = (
        # NULLs are distinct under both Postgres and SQLite unique
        # constraints (matches Device's own unique constraints), so
        # commands issued without an idempotency key never collide with
        # each other — only a *repeated* key for the same org+device does.
        UniqueConstraint(
            "organization_id",
            "device_id",
            "idempotency_key",
            name="uq_device_command_org_device_idempotency",
        ),
        Index("ix_device_commands_org_device", "organization_id", "device_id"),
        Index("ix_device_commands_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command_type: Mapped[DeviceCommandType] = mapped_column(
        Enum(DeviceCommandType, name="device_command_type"), nullable=False, index=True
    )
    status: Mapped[DeviceCommandStatus] = mapped_column(
        Enum(DeviceCommandStatus, name="device_command_status"),
        nullable=False,
        default=DeviceCommandStatus.PENDING,
        index=True,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Command-specific parameters supplied by the caller (e.g. a "
        "lock-screen message). Never contains secrets.",
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment='"mock" | "android" | "apple" — recorded at dispatch time, '
        "even if platform_provider_mode later changes.",
    )
    provider_command_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Sanitized provider response. Must NEVER contain provider "
        "credentials or device secrets — see docs/security/phase-6-security-review.md.",
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL is reserved for a future system-initiated command; "
        "every Phase 6 command is admin-requested.",
    )
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set on any terminal transition (COMPLETED/FAILED/CANCELLED/EXPIRED), "
        "not only success — the name marks 'no longer active', not 'succeeded'.",
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    organization = relationship("Organization", lazy="noload")
    device = relationship("Device", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<DeviceCommand id={self.id} org={self.organization_id} "
            f"device={self.device_id} type={self.command_type} status={self.status}>"
        )
