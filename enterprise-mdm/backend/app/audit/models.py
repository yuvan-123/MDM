"""Audit log and security event ORM models.

RULES:
- AuditLog is insert-only. No API exposes UPDATE or DELETE on this table.
- SecurityEvent is insert-only and separate from ordinary audit logs to
  allow different retention/alerting policies.
- Neither table ever stores: passwords, tokens, MFA secrets, or raw PII
  beyond what is necessary for audit trail purposes (user IDs, IPs).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, UUIDPrimaryKeyMixin

# Dialect-compatible JSON type: uses Postgres JSONB in production, fallback to JSON in SQLite tests.
JSONType = JSON().with_variant(JSONB, "postgresql")


class AuditEventType(str, enum.Enum):
    # Authentication events
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    LOGOUT_ALL = "LOGOUT_ALL"
    TOKEN_REFRESH = "TOKEN_REFRESH"
    TOKEN_REUSE_DETECTED = "TOKEN_REUSE_DETECTED"

    # Password events
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    PASSWORD_RESET_COMPLETED = "PASSWORD_RESET_COMPLETED"

    # Email verification
    EMAIL_VERIFIED = "EMAIL_VERIFIED"
    EMAIL_VERIFICATION_SENT = "EMAIL_VERIFICATION_SENT"

    # Session management
    SESSION_REVOKED = "SESSION_REVOKED"

    # Account status
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    ACCOUNT_UNLOCKED = "ACCOUNT_UNLOCKED"

    # RBAC events
    ROLE_ASSIGNED = "ROLE_ASSIGNED"
    ROLE_REMOVED = "ROLE_REMOVED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    # User management
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_STATUS_CHANGED = "USER_STATUS_CHANGED"

    # Organization management (Phase 3)
    ORGANIZATION_CREATED = "ORGANIZATION_CREATED"
    ORGANIZATION_UPDATED = "ORGANIZATION_UPDATED"
    ORGANIZATION_SUSPENDED = "ORGANIZATION_SUSPENDED"
    ORGANIZATION_DISABLED = "ORGANIZATION_DISABLED"
    ORGANIZATION_ACCESS_DENIED = "ORGANIZATION_ACCESS_DENIED"

    # Organization membership (Phase 3)
    MEMBER_INVITED = "MEMBER_INVITED"
    MEMBER_ACCEPTED = "MEMBER_ACCEPTED"
    MEMBER_UPDATED = "MEMBER_UPDATED"
    MEMBER_REMOVED = "MEMBER_REMOVED"
    MEMBER_SUSPENDED = "MEMBER_SUSPENDED"

    # Device inventory (Phase 4)
    DEVICE_CREATED = "DEVICE_CREATED"
    DEVICE_UPDATED = "DEVICE_UPDATED"
    DEVICE_ASSIGNED = "DEVICE_ASSIGNED"
    DEVICE_UNASSIGNED = "DEVICE_UNASSIGNED"
    DEVICE_BLOCKED = "DEVICE_BLOCKED"
    DEVICE_UNBLOCKED = "DEVICE_UNBLOCKED"
    DEVICE_RETIRED = "DEVICE_RETIRED"
    DEVICE_DELETED = "DEVICE_DELETED"
    DEVICE_SENSITIVE_DATA_VIEWED = "DEVICE_SENSITIVE_DATA_VIEWED"

    # Enrollment (Phase 5)
    ENROLLMENT_TOKEN_CREATED = "ENROLLMENT_TOKEN_CREATED"
    ENROLLMENT_TOKEN_REVOKED = "ENROLLMENT_TOKEN_REVOKED"
    DEVICE_ENROLLMENT_REGISTERED = "DEVICE_ENROLLMENT_REGISTERED"
    DEVICE_ENROLLMENT_APPROVED = "DEVICE_ENROLLMENT_APPROVED"
    DEVICE_ENROLLMENT_REJECTED = "DEVICE_ENROLLMENT_REJECTED"
    DEVICE_ENROLLMENT_REVOKED = "DEVICE_ENROLLMENT_REVOKED"

    # Device groups (Phase 5)
    DEVICE_GROUP_CREATED = "DEVICE_GROUP_CREATED"
    DEVICE_GROUP_UPDATED = "DEVICE_GROUP_UPDATED"
    DEVICE_GROUP_DELETED = "DEVICE_GROUP_DELETED"
    DEVICE_GROUP_MEMBER_ADDED = "DEVICE_GROUP_MEMBER_ADDED"
    DEVICE_GROUP_MEMBER_REMOVED = "DEVICE_GROUP_MEMBER_REMOVED"

    # Policies (Phase 5)
    POLICY_CREATED = "POLICY_CREATED"
    POLICY_UPDATED = "POLICY_UPDATED"
    POLICY_DELETED = "POLICY_DELETED"
    POLICY_ASSIGNED = "POLICY_ASSIGNED"
    POLICY_UNASSIGNED = "POLICY_UNASSIGNED"

    # Compliance (Phase 5)
    COMPLIANCE_EVALUATED = "COMPLIANCE_EVALUATED"

    # Device commands (Phase 6)
    DEVICE_COMMAND_CREATED = "DEVICE_COMMAND_CREATED"
    DEVICE_COMMAND_DISPATCHED = "DEVICE_COMMAND_DISPATCHED"
    DEVICE_COMMAND_COMPLETED = "DEVICE_COMMAND_COMPLETED"
    DEVICE_COMMAND_FAILED = "DEVICE_COMMAND_FAILED"
    DEVICE_COMMAND_CANCELLED = "DEVICE_COMMAND_CANCELLED"
    DEVICE_COMMAND_EXPIRED = "DEVICE_COMMAND_EXPIRED"

    # Provider-device link administration (Phase 10)
    PROVIDER_DEVICE_LINK_CREATED = "PROVIDER_DEVICE_LINK_CREATED"
    PROVIDER_DEVICE_LINK_UPDATED = "PROVIDER_DEVICE_LINK_UPDATED"
    PROVIDER_DEVICE_LINK_DELETED = "PROVIDER_DEVICE_LINK_DELETED"

    # Background job / provider-event manual operations (Phase 10)
    BACKGROUND_JOB_RETRIED = "BACKGROUND_JOB_RETRIED"
    PROVIDER_EVENT_RETRIED = "PROVIDER_EVENT_RETRIED"

    # Policy violation remediation (Phase 13) — deliberately NOT including
    # a per-evaluation "violation detected" event: that would fire on
    # every heartbeat for a persistently non-compliant device. Only
    # state-changing remediation actions and manual admin actions are
    # audited — see app/compliance/service.py.
    COMPLIANCE_REMEDIATION_TRIGGERED = "COMPLIANCE_REMEDIATION_TRIGGERED"
    COMPLIANCE_REMEDIATION_RETRIED = "COMPLIANCE_REMEDIATION_RETRIED"
    COMPLIANCE_REMEDIATION_DISABLED = "COMPLIANCE_REMEDIATION_DISABLED"
    COMPLIANCE_REMEDIATION_ENABLED = "COMPLIANCE_REMEDIATION_ENABLED"
    COMPLIANCE_VIOLATION_RESOLVED = "COMPLIANCE_VIOLATION_RESOLVED"

    # Application management (Phase 14) — deployment/removal progress
    # itself is NOT separately audited beyond trigger/failure: the
    # underlying DeviceCommand already emits DEVICE_COMMAND_CREATED/
    # DISPATCHED/COMPLETED/FAILED for every state change, and duplicating
    # that here would just double-log the same transition under a second
    # name (see app/applications/service.py).
    APPLICATION_CREATED = "APPLICATION_CREATED"
    APPLICATION_UPDATED = "APPLICATION_UPDATED"
    APPLICATION_DELETED = "APPLICATION_DELETED"
    APPLICATION_ASSIGNED = "APPLICATION_ASSIGNED"
    APPLICATION_UNASSIGNED = "APPLICATION_UNASSIGNED"
    APPLICATION_DEPLOYMENT_TRIGGERED = "APPLICATION_DEPLOYMENT_TRIGGERED"
    APPLICATION_REMOVAL_TRIGGERED = "APPLICATION_REMOVAL_TRIGGERED"


class SecurityEventSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SecurityEventType(str, enum.Enum):
    BRUTE_FORCE_DETECTED = "BRUTE_FORCE_DETECTED"
    REFRESH_TOKEN_REUSE = "REFRESH_TOKEN_REUSE"
    ACCOUNT_LOCKED_BRUTE_FORCE = "ACCOUNT_LOCKED_BRUTE_FORCE"
    REPEATED_PERMISSION_FAILURES = "REPEATED_PERMISSION_FAILURES"
    SUSPICIOUS_IP_ACTIVITY = "SUSPICIOUS_IP_ACTIVITY"
    INVALID_TOKEN_PRESENTED = "INVALID_TOKEN_PRESENTED"

    # Device inventory (Phase 4) — reserved for future automated detection;
    # no code path raises these yet (see STEP 20 of the Phase 4 spec).
    DEVICE_ENROLLMENT_FAILED = "DEVICE_ENROLLMENT_FAILED"
    DEVICE_CHECKIN_FAILED = "DEVICE_CHECKIN_FAILED"
    DEVICE_NON_COMPLIANT = "DEVICE_NON_COMPLIANT"
    DEVICE_COMPROMISED = "DEVICE_COMPROMISED"
    DEVICE_LOST = "DEVICE_LOST"
    DEVICE_BLOCKED = "DEVICE_BLOCKED"


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Immutable audit trail. INSERT-ONLY — no update/delete permitted.

    actor_user_id: the authenticated user who performed the action.
                   NULL for system-initiated or unauthenticated events
                   (e.g. failed login — we don't know who they are).
    target_user_id: the user the action was performed on (if applicable).
    organization_id: NULL for platform-level events; populated from Phase 3.
    metadata: arbitrary JSON dict for event-specific details. Must NEVER
              contain passwords, tokens, or secrets.
    """

    __tablename__ = "audit_logs"

    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, name="audit_event_type"),
        nullable=False,
        index=True,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="NULL for platform events; org FK added in Phase 3.",
    )
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType,
        nullable=True,
        comment="Event-specific JSON. Must NEVER contain secrets or tokens.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class SecurityEvent(Base, UUIDPrimaryKeyMixin):
    """Security-relevant events requiring separate handling / alerting."""

    __tablename__ = "security_events"

    event_type: Mapped[SecurityEventType] = mapped_column(
        Enum(SecurityEventType, name="security_event_type"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    severity: Mapped[SecurityEventSeverity] = mapped_column(
        Enum(SecurityEventSeverity, name="security_event_severity"),
        nullable=False,
        default=SecurityEventSeverity.MEDIUM,
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
