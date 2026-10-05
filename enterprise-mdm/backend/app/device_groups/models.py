"""DeviceGroup / DeviceGroupMember ORM models.

Groups are org-scoped containers for devices, used for bulk actions,
reporting, and (from this phase) policy assignment scope. Membership is a
plain many-to-many join — no per-membership status, unlike
OrganizationMembership; a device is either in a group or it isn't.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DeviceGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "device_groups"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_device_group_org_name"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization = relationship("Organization", lazy="noload")

    def __repr__(self) -> str:
        return f"<DeviceGroup id={self.id} org={self.organization_id} name={self.name!r}>"


class DeviceGroupMember(Base):
    __tablename__ = "device_group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("device_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        primary_key=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    device = relationship("Device", lazy="noload")
