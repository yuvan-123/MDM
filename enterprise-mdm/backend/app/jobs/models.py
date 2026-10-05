"""Background job ORM model (Phase 7).

`BackgroundJob` is a Postgres-backed work queue — the replacement for the
never-wired Celery dependency (see docs/architecture/background-jobs.md for
why Celery was rejected). `app/workers/loop.py` polls this table for
PENDING rows and processes them one at a time; there is no separate
message broker.

Like DeviceCommand (Phase 6), one row per unit of work, moved forward
through the state machine on `JobStatus` below and never mutated back to a
prior state except the deliberate CLAIMED -> PENDING retry transition.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

JSONType = JSON().with_variant(JSONB, "postgresql")


class JobType(str, enum.Enum):
    # Evaluate one device's compliance against its resolved policy — see
    # app/workers/loop.py::_process_compliance_evaluation. Enqueued directly
    # by a device heartbeat, or as the terminal step of a
    # POLICY_REEVALUATION_FANOUT page.
    COMPLIANCE_EVALUATION = "COMPLIANCE_EVALUATION"
    # Pages through every device covered by a DEVICE_GROUP/USER/ORGANIZATION
    # scoped policy assignment change, 200 devices at a time, re-enqueueing
    # itself with an advancing cursor until the scope is exhausted or the
    # page cap is hit. A DEVICE-scoped assignment change schedules
    # COMPLIANCE_EVALUATION directly instead — see
    # app/jobs/service.py::JobService.enqueue_policy_reevaluation.
    POLICY_REEVALUATION_FANOUT = "POLICY_REEVALUATION_FANOUT"
    # Dispatches one already-created DeviceCommand (Phase 8) — decouples
    # command creation from the (potentially slow/unreachable) provider
    # call, so the issuing HTTP request never blocks on it. Payload:
    # {"device_command_id": ...}. See
    # app/commands/service.py::CommandService.process_dispatch_job and
    # docs/architecture/background-jobs.md §7.
    COMMAND_DISPATCH = "COMMAND_DISPATCH"
    # Creates the DeviceCommand that remediates one PolicyViolation (Phase
    # 13) — decouples "decide remediation is needed" (evaluate_device,
    # synchronous) from "actually create the command" the same way
    # COMMAND_DISPATCH already decouples command creation from provider
    # dispatch. Payload: {"violation_id": ..., "command_type": ...}. See
    # app/compliance/service.py and app/workers/loop.py::_process_compliance_remediation.
    COMPLIANCE_REMEDIATION = "COMPLIANCE_REMEDIATION"


class JobStatus(str, enum.Enum):
    """PENDING -> CLAIMED (a worker cycle claims it) -> COMPLETED
    PENDING -> CLAIMED -> PENDING (transient failure, attempts remaining —
        rescheduled at a backed-off `scheduled_at`)
    PENDING -> CLAIMED -> FAILED (permanent failure, or attempts exhausted)
    COMPLETED / FAILED: terminal.
    """

    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BackgroundJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "background_jobs"
    __table_args__ = (
        Index("ix_background_jobs_status_scheduled", "status", "scheduled_at"),
        Index("ix_background_jobs_org_type", "organization_id", "job_type"),
        Index("ix_background_jobs_dedup", "organization_id", "job_type", "dedup_key"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="background_job_type"), nullable=False, index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="background_job_status"),
        nullable=False,
        default=JobStatus.PENDING,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, comment="Job-type-specific parameters. Never contains secrets."
    )
    dedup_key: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="Set only on the initially-enqueued job for a given target "
        "(e.g. 'compliance_evaluation:<device_id>'); a re-enqueue that "
        "advances a fanout cursor leaves this NULL, since the whole point of "
        "dedup is to collapse repeated *triggers*, not the pagination chain "
        "a single trigger produces. See JobService._enqueue.",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Not eligible for claim_next() until now() >= this — used "
        "both for the initial delay (none, today) and retry backoff.",
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<BackgroundJob id={self.id} type={self.job_type} status={self.status}>"
