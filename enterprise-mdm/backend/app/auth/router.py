"""Authentication API router.

Endpoints:
  POST   /api/v1/auth/login
  POST   /api/v1/auth/refresh
  POST   /api/v1/auth/logout
  POST   /api/v1/auth/logout-all
  GET    /api/v1/auth/me
  POST   /api/v1/auth/change-password
  POST   /api/v1/auth/forgot-password
  POST   /api/v1/auth/reset-password
  GET    /api/v1/auth/sessions
  DELETE /api/v1/auth/sessions/{session_id}

Transport:
  Sets HttpOnly cookies for browser clients. Also returns access token in
  body for non-browser Bearer clients.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import AuditEventType
from app.auth.cookies import (
    clear_auth_cookies,
    get_refresh_token_from_request,
    set_auth_cookies,
)
from app.auth.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    ResetPasswordRequest,
    SessionResponse,
    UserResponse,
)
from app.auth.service import AuthService
from app.core.errors import AppError
from app.database.session import get_db_session
from app.organizations.dependencies import resolve_organization_context
from app.organizations.schemas import (
    OrganizationContextResponse,
    OrganizationResponse,
    SelectOrganizationRequest,
)
from app.rbac.dependencies import CurrentUser, get_current_user
from app.repositories.audit_repository import AuditRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.token_repository import TokenRepository
from app.repositories.user_repository import UserRepository

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_auth_service(db: AsyncSession = Depends(get_db_session)) -> AuthService:
    return AuthService(
        user_repo=UserRepository(db),
        session_repo=SessionRepository(db),
        token_repo=TokenRepository(db),
        audit_repo=AuditRepository(db),
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: AuthService = Depends(_get_auth_service),
    db: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    """Authenticate email+password, set cookies, return user + CSRF token."""
    access_token, raw_refresh, csrf_token, session_id = await service.login(
        email=body.email,
        password=body.password,
        request=request,
    )

    set_auth_cookies(
        response=response,
        access_token=access_token,
        refresh_token=raw_refresh,
        csrf_token=csrf_token,
    )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_email(body.email)
    assert user is not None  # login guaranteed user exists

    return LoginResponse(
        user=UserResponse.model_validate(user),
        csrf_token=csrf_token,
        access_token=access_token,
    )


@router.post("/refresh", response_model=LoginResponse)
async def refresh(
    request: Request,
    response: Response,
    service: AuthService = Depends(_get_auth_service),
    db: AsyncSession = Depends(get_db_session),
) -> LoginResponse:
    """Rotate refresh token and issue new token cookies.

    Requires valid refresh token cookie. If a revoked token is re-submitted,
    this endpoint detects token reuse, invalidates all sessions, and raises 401.
    """
    raw_refresh = get_refresh_token_from_request(request)
    if not raw_refresh:
        raise AppError(
            code="NOT_AUTHENTICATED",
            message="Refresh token missing.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    new_access, new_refresh, new_csrf = await service.refresh(
        raw_refresh_token=raw_refresh,
        request=request,
    )

    set_auth_cookies(
        response=response,
        access_token=new_access,
        refresh_token=new_refresh,
        csrf_token=new_csrf,
    )

    # Decode access token payload to get user ID for response body
    from app.auth.tokens import decode_access_token

    payload = decode_access_token(new_access)
    user_id = uuid.UUID(payload["sub"])

    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(user_id)
    assert user is not None

    return LoginResponse(
        user=UserResponse.model_validate(user),
        csrf_token=new_csrf,
        access_token=new_access,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: AuthService = Depends(_get_auth_service),
) -> None:
    """Revoke the current session and clear cookies."""
    await service.logout(
        session_id=current_user.session_id,
        user_id=current_user.user_id,
        request=request,
    )
    clear_auth_cookies(response)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    response: Response,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: AuthService = Depends(_get_auth_service),
) -> None:
    """Revoke ALL sessions for the current user and clear cookies."""
    await service.logout_all(user_id=current_user.user_id, request=request)
    clear_auth_cookies(response)


@router.get("/me", response_model=MeResponse)
async def get_me(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db_session),
) -> MeResponse:
    """Return the current user's profile and effective permissions."""
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(current_user.user_id)
    assert user is not None

    return MeResponse(
        user=UserResponse.model_validate(user),
        session_id=current_user.session_id,
        permissions=sorted(current_user.permissions),
    )


@router.post("/select-organization", response_model=OrganizationContextResponse)
async def select_organization(
    body: SelectOrganizationRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db_session),
) -> OrganizationContextResponse:
    """Validate the caller's access to *organization_id* and return the
    resolved organization + effective org-scoped permissions.

    This does NOT create server-side session state — the frontend caches
    the result and re-sends `X-Organization-ID` on every subsequent
    tenant-scoped request, which is re-validated from scratch every time
    (see app/organizations/dependencies.py). This endpoint exists purely so
    the org selector UI can validate a choice and fetch permissions in one
    round trip; it is a lookup, not a state mutation.
    """
    org_context = await resolve_organization_context(db, current_user, body.organization_id)
    org_repo = OrganizationRepository(db)
    org = await org_repo.get_by_id(org_context.organization_id)
    assert org is not None  # resolve_organization_context already validated existence

    return OrganizationContextResponse(
        organization=OrganizationResponse.model_validate(org),
        membership_id=org_context.membership_id,
        is_platform_super_admin=org_context.is_platform_super_admin,
        permissions=sorted(org_context.permissions),
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    service: AuthService = Depends(_get_auth_service),
) -> None:
    """Change current user's password. Revokes other active sessions."""
    await service.change_password(
        user_id=current_user.user_id,
        current_session_id=current_user.session_id,
        old_password=body.old_password,
        new_password=body.new_password,
        request=request,
    )


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(
    body: ForgotPasswordRequest,
    request: Request,
    service: AuthService = Depends(_get_auth_service),
) -> None:
    """Initiate password reset flow.

    Always returns 204 regardless of whether the email exists.
    """
    base_url = str(request.base_url).rstrip("/")
    await service.request_password_reset(
        email=body.email,
        request=request,
        base_url=base_url,
    )


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    service: AuthService = Depends(_get_auth_service),
) -> None:
    """Reset password using a valid reset token from email."""
    await service.reset_password(
        raw_token=body.token,
        new_password=body.new_password,
        request=request,
    )


@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionResponse]:
    """List all active sessions for the current user."""
    session_repo = SessionRepository(db)
    sessions = await session_repo.list_active_for_user(current_user.user_id)

    results: list[SessionResponse] = []
    for s in sessions:
        item = SessionResponse.model_validate(s)
        item.is_current = s.id == current_user.session_id
        results.append(item)
    return results


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Revoke a specific session belonging to the current user."""
    session_repo = SessionRepository(db)
    target = await session_repo.get_by_id(session_id)
    if target is None or target.user_id != current_user.user_id:
        raise AppError(
            code="NOT_FOUND",
            message="Session not found.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    await session_repo.revoke(session_id)
    audit_repo = AuditRepository(db)
    await audit_repo.log_event(
        event_type=AuditEventType.SESSION_REVOKED,
        actor_user_id=current_user.user_id,
        ip_address=(request.client.host if request.client else None),
        metadata={"revoked_session_id": str(session_id)},
    )
