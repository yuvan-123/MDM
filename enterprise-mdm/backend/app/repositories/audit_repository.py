"""Audit and security event repository — insert-only."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import (
    AuditEventType,
    AuditLog,
    SecurityEvent,
    SecurityEventSeverity,
    SecurityEventType,
)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log_event(
        self,
        *,
        event_type: AuditEventType,
        actor_user_id: uuid.UUID | None = None,
        target_user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
        organization_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Insert an immutable audit log entry.

        This method must NEVER be used to log passwords, tokens, or secrets.
        The metadata dict is persisted as-is; callers are responsible for
        sanitizing it before passing here.
        """
        entry = AuditLog(
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
            organization_id=organization_id,
            event_metadata=metadata,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def log_security_event(
        self,
        *,
        event_type: SecurityEventType,
        user_id: uuid.UUID | None = None,
        ip_address: str | None = None,
        severity: SecurityEventSeverity = SecurityEventSeverity.MEDIUM,
        details: dict[str, Any] | None = None,
    ) -> SecurityEvent:
        """Insert an immutable security event entry."""
        event = SecurityEvent(
            event_type=event_type,
            user_id=user_id,
            ip_address=ip_address,
            severity=severity,
            details=details,
            created_at=datetime.now(tz=UTC),
        )
        self._session.add(event)
        await self._session.flush()
        return event
