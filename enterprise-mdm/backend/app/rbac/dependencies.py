"""FastAPI dependencies for authentication and authorization.

CurrentUser is the secure, server-derived representation of the
authenticated principal. It is NEVER constructed from client-supplied
data — always from a validated JWT + active database session.

Usage in route handlers:

    @router.get("/protected")
    async def endpoint(
        current_user: CurrentUser = Depends(get_current_user),
        _: None = Depends(require_permission("device.read")),
    ) -> ...:
        ...

Or combined:

    @router.delete("/device/{id}")
    async def delete_device(
        current_user: Annotated[CurrentUser, Depends(require_permission("device.wipe"))],
        ...
    ) -> ...:
        ...
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Annotated

import jwt
from fastapi import Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventType
from app.auth.cookies import get_access_token_from_request
from app.auth.tokens import decode_access_token
from app.core.errors import AppError
from app.core.logging import get_logger
from app.database.session import get_db_session
from app.repositories.audit_repository import AuditRepository
from app.repositories.rbac_repository import RBACRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.users.models import UserStatus

logger = get_logger(__name__)


@dataclass
class CurrentUser:
    """Authenticated principal attached to a request.

    This object is the ONLY source of truth for authenticated identity.
    Never trust a user_id from the request body.
    """

    user_id: uuid.UUID
    email: str
    status: UserStatus
    session_id: uuid.UUID
    first_name: str
    last_name: str
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> CurrentUser:
    """FastAPI dependency: validate the access token and return CurrentUser.

    Validates:
      1. Access token signature and expiry.
      2. Session exists and is active (not revoked, not expired).
      3. User exists and is in an active-capable status.
    """
    token = get_access_token_from_request(request)
    if token is None:
        raise AppError(
            code="NOT_AUTHENTICATED",
            message="Authentication required.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise AppError(
            code="TOKEN_EXPIRED",
            message="Access token has expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    except jwt.PyJWTError:
        raise AppError(
            code="INVALID_TOKEN",
            message="Authentication token is invalid.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_id = uuid.UUID(payload["sub"])
    session_id = uuid.UUID(payload["session_id"])

    session_repo = SessionRepository(db)
    db_session = await session_repo.get_active_by_id(session_id)
    if db_session is None:
        raise AppError(
            code="SESSION_INVALID",
            message="Session is invalid or expired.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    if user is None or user.status in (UserStatus.DISABLED, UserStatus.SUSPENDED):
        raise AppError(
            code="ACCOUNT_INACTIVE",
            message="Your account is not active.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    rbac_repo = RBACRepository(db)
    permissions = await rbac_repo.get_permissions_for_user(user_id)

    return CurrentUser(
        user_id=user_id,
        email=user.email,
        status=user.status,
        session_id=session_id,
        first_name=user.first_name,
        last_name=user.last_name,
        permissions=permissions,
    )


def require_permission(permission: str) -> type[CurrentUser]:
    """Return a FastAPI dependency that enforces a specific permission.

    Usage:
        Depends(require_permission("device.wipe"))

    The dependency first resolves the current user (via get_current_user),
    then checks the permission. A PERMISSION_DENIED audit event is logged
    on failure.
    """

    async def _check(
        request: Request,
        current_user: Annotated[CurrentUser, Depends(get_current_user)],
        db: AsyncSession = Depends(get_db_session),
    ) -> CurrentUser:
        if not current_user.has_permission(permission):
            audit_repo = AuditRepository(db)
            await audit_repo.log_event(
                event_type=AuditEventType.PERMISSION_DENIED,
                actor_user_id=current_user.user_id,
                ip_address=(request.client.host if request.client else None),
                request_id=getattr(request.state, "request_id", None),
                metadata={
                    "required_permission": permission,
                    "path": request.url.path,
                    "method": request.method,
                },
            )
            logger.warning(
                "permission_denied",
                user_id=str(current_user.user_id),
                required=permission,
                path=request.url.path,
            )
            raise AppError(
                code="PERMISSION_DENIED",
                message="You do not have permission to perform this action.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return current_user

    return _check  # type: ignore[return-value]
